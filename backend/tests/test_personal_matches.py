from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.db import Base
from app import personal_matches as personal
from app.rating import calculate
from app.service import ingest_page, serialize_profile, upsert_profile
from app.source import HistoryPage
from tests.helpers import login, match, profile


def board(match_id="2188356974", *, mode="competitive", kills=22, deaths=7, assists=0, nickname="hamy"):
    return {"mode": mode, "selected_index": 0, "requested_nickname": nickname,
            "match_id": match_id, "map_name": "Sandstone", "score": [8, 6],
            "played_at": None, "date_label": "Вчера в 13:41", "image_fingerprint": "a" * 64,
            "rows": [{"nickname": nickname, "aliases": ["harny"], "uid": None, "team": "ct",
                      "kills": kills, "deaths": deaths, "assists": assists, "game_score": 46, "rank": 1},
                     {"nickname": "lov|Nagi", "aliases": [], "uid": None, "team": "ct",
                      "kills": 12, "deaths": 11, "assists": 3},
                     {"nickname": "215327206", "aliases": [], "uid": None, "team": "t",
                      "kills": 4, "deaths": 9, "assists": 2},
                     {"nickname": "Нечёткая строка", "aliases": [], "uid": None, "team": "t",
                      "kills": None, "deaths": 8, "assists": None}]}


def test_screenshot_persists_complete_private_board_without_polluting_game_history(db_factory):
    with db_factory() as db:
        result = personal.save_screenshot_match(db, 100, board())
        db.commit()
        screenshot_id = result["match"]["screenshot_id"]
        assert result["is_new_best"] is True
        assert db.get(m.User, 100).primary_uid is None
        assert db.scalar(select(func.count()).select_from(m.Match)) == 0
        assert db.scalar(select(func.count()).select_from(m.Participant)) == 0
        assert db.scalar(select(func.count()).select_from(m.Player)) == 0
    with db_factory() as db:
        own = db.get(m.User, 100)
        best = personal.get_personal_best(db, own, "competitive")
        assert best["screenshot_id"] == screenshot_id
        assert best["source"] == "screenshot" and best["rating_tier"] == "match"
        assert best["rating_inputs"] == {"kills": 22, "deaths": 7, "assists": 0, "rounds": None}
        assert best["rating_parameters"]["opponent_adjustment"] is False
        assert best["ended_at"] is None and best["date_label"] == "Вчера в 13:41"
        assert best["loaded_at"] is not None
        assert len(best["players"]) == 4
        assert best["players"][0]["user_confirmed"] is True
        assert all(not row["user_confirmed"] for row in best["players"][1:])
        assert best["players"][2]["uid"] is None  # Numeric nickname is not a UID.
        assert best["players"][3]["rating"] is None
        assert best["players"][0]["game_score"] == 46 and best["players"][0]["rank"] == 1
        serialized = json.dumps(db.get(m.ScreenshotMatch, screenshot_id).snapshot)
        assert "image_fingerprint" not in serialized and "image_bytes" not in serialized
        db.commit()


def test_strict_bigger_replaces_tie_and_lower_keep_existing_match_and_modes_separate(db):
    first = personal.save_screenshot_match(db, 100, board("first", kills=15, deaths=15, assists=4))
    first_id = first["best"]["id"]
    tie = personal.save_screenshot_match(db, 100, board("tie", kills=15, deaths=15, assists=4))
    lower = personal.save_screenshot_match(db, 100, board("lower", kills=1, deaths=20, assists=0))
    assert tie["best"]["id"] == lower["best"]["id"] == first_id
    assert not tie["is_new_best"] and not lower["is_new_best"]
    better = personal.save_screenshot_match(db, 100, board("better", kills=30, deaths=10, assists=5))
    assert better["is_new_best"] and better["best"]["match_id"] == "better"
    allies = personal.save_screenshot_match(db, 100, board("allies", mode="allies", kills=8, deaths=8, assists=2))
    assert allies["is_new_best"]
    assert personal.get_personal_best(db, db.get(m.User, 100), "competitive")["match_id"] == "better"
    assert personal.get_personal_best(db, db.get(m.User, 100), "allies")["match_id"] == "allies"


def test_actual_decimal8_improvement_can_replace_equal_two_digit_display(db):
    first = personal.save_screenshot_match(db, 100, board("first", kills=16, deaths=10, assists=3))
    second = personal.save_screenshot_match(db, 100, board("second", kills=17, deaths=11, assists=2))
    assert first["match"]["rating_display"] == second["match"]["rating_display"] == "1.10"
    assert first["match"]["rating_color"] == second["match"]["rating_color"] == "white"
    assert second["is_new_best"] and second["best"]["match_id"] == "second"


def test_duplicates_and_downward_correction_rebuild_best_instead_of_keeping_wrong_record(db):
    payload = board("first", kills=30, deaths=3, assists=10)
    first = personal.save_screenshot_match(db, 100, payload)
    duplicate = personal.save_screenshot_match(db, 100, deepcopy(payload))
    assert duplicate["match"]["id"] == first["match"]["id"]
    assert not duplicate["is_new_best"]
    second = personal.save_screenshot_match(db, 100, board("second", kills=20, deaths=10, assists=5))
    corrected = deepcopy(payload)
    corrected["rows"][0].update(kills=1, deaths=20, assists=0)
    corrected["rows"][1]["kills"] = 13
    correction = personal.save_screenshot_match(db, 100, corrected)
    assert correction["match"]["id"] == first["match"]["id"]
    assert correction["best"]["id"] == second["match"]["id"]
    assert not correction["is_new_best"]
    assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 2


def test_corrected_winner_context_refreshes_without_replacing_a_tie(db):
    payload = board("original")
    result = personal.save_screenshot_match(db, 100, payload)
    personal.save_screenshot_match(db, 100, board("tie"))
    payload["rows"][1]["game_score"] = 99
    updated = personal.save_screenshot_match(db, 100, payload)
    assert updated["best"]["id"] == result["best"]["id"]
    assert updated["best"]["players"][1]["game_score"] == 99
    assert not updated["is_new_best"]


def test_missing_match_id_uses_image_fingerprint_and_corrections_preserve_identity(db):
    payload = board(None)
    first = personal.save_screenshot_match(db, 100, payload)
    payload["rows"][0]["kills"] = 25
    corrected = personal.save_screenshot_match(db, 100, payload)
    assert corrected["match"]["id"] == first["match"]["id"] and corrected["is_new_best"]
    payload["image_fingerprint"] = "b" * 64
    separate_image = personal.save_screenshot_match(db, 100, payload)
    assert separate_image["match"]["id"] != first["match"]["id"]
    assert not separate_image["is_new_best"]


def test_metadata_fallback_and_casefold_requested_nickname_deduplicate(db):
    payload = board(None)
    payload.pop("image_fingerprint")
    first = personal.save_screenshot_match(db, 100, payload)
    payload["rows"][0]["assists"] = 3
    payload["requested_nickname"] = "HAMY"
    corrected = personal.save_screenshot_match(db, 100, payload)
    assert corrected["match"]["id"] == first["match"]["id"]


def test_unified_kda_comparison_uses_both_sources_without_changing_automatic_ratings(db):
    owner = personal.ensure_personal_user(db, 100)
    player = upsert_profile(db, profile())
    owner.primary_uid = player.uid
    ingest_page(db, player.uid, "competitive", HistoryPage([
        match("round-performance", kills=9, deaths=8, assists=1, rounds=8),
    ]))
    automatic = db.scalar(select(m.Participant))
    original_rating = automatic.rating
    assert original_rating > Decimal("1.00")
    result = personal.save_screenshot_match(db, 100, board("screenshot", kills=15, deaths=15, assists=4))
    assert result["best"]["source"] == "screenshot" and result["best"]["rating_display"] == "1.00"
    assert automatic.rating == original_rating  # No main-stat/history rewrite.
    ingest_page(db, player.uid, "competitive", HistoryPage([
        match("future-auto", day=2, kills=30, deaths=10, assists=6),
    ]))
    best = personal.get_personal_best(db, owner, "competitive")
    assert best["source"] == "automatic" and best["match_id"] == "future-auto"
    assert best["rating"] == float(calculate("competitive", 30, 10, 6, rounds=None)["value"])
    assert best["original_rating"] is not None and best["original_rating_tier"] == "rounds"
    summary = serialize_profile(db, player, "competitive", "last20")["summary"]
    assert summary["count"] == 2
    assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 1


def test_automatic_tie_preserves_existing_screenshot_winner(db):
    saved = personal.save_screenshot_match(db, 100, board("screenshot", kills=15, deaths=15, assists=4))
    owner = db.get(m.User, 100)
    player = upsert_profile(db, profile())
    owner.primary_uid = player.uid
    ingest_page(db, player.uid, "competitive", HistoryPage([match("equal-auto")]))
    assert personal.get_personal_best(db, owner, "competitive")["id"] == saved["best"]["id"]


def test_formula_version_change_rebuilds_all_candidates_from_raw_kda(db, monkeypatch):
    personal.save_screenshot_match(db, 100, board("first"))
    owner = db.get(m.User, 100)
    stored = db.get(m.PersonalBest, (100, "competitive"))
    stored.formula_version = "obsolete"
    stored.rating = Decimal("99")
    snapshot = deepcopy(stored.snapshot)
    snapshot["rating"] = 99
    stored.snapshot = snapshot
    db.flush()
    monkeypatch.setattr(personal, "VERSION", "test-v2")
    best = personal.get_personal_best(db, owner, "competitive")
    assert best["formula_version"] == "test-v2" and best["rating"] < 99
    assert best["rating_inputs"]["kills"] == 22
    assert db.get(m.PersonalBest, (100, "competitive")).formula_version == "test-v2"


def test_switching_primary_uid_recomputes_automatic_best_without_rebinding_from_screenshot(db):
    owner = personal.ensure_personal_user(db, 100)
    first, second = upsert_profile(db, profile("111")), upsert_profile(db, profile("222"))
    owner.primary_uid = first.uid
    ingest_page(db, first.uid, "competitive", HistoryPage([match("first-auto", uid=first.uid, kills=40)]))
    assert personal.get_personal_best(db, owner, "competitive")["player_uid"] == "111"
    owner.primary_uid = second.uid
    ingest_page(db, second.uid, "competitive", HistoryPage([match("second-auto", uid=second.uid, kills=15)]))
    assert personal.get_personal_best(db, owner, "competitive")["player_uid"] == "222"
    personal.save_screenshot_match(db, 100, board(nickname="215327206"))
    assert owner.primary_uid == "222"


def test_private_archive_api_and_best_require_server_session_and_ignore_foreign_owners(client, db_factory):
    for endpoint in ("/api/me/best", "/api/me/screenshot-matches", "/api/me/screenshot-matches/1"):
        assert client.get(endpoint).status_code == 401
    with db_factory() as db:
        first = personal.save_screenshot_match(db, 100, board())
        personal.save_screenshot_match(db, 200, board("other", kills=40))
        db.commit()
    login(client, 200)
    assert client.get(f"/api/me/screenshot-matches/{first['match']['screenshot_id']}").status_code == 404
    assert client.get("/api/me/best?telegram_id=100").json()["best"]["match_id"] == "other"
    assert client.get("/api/me/screenshot-matches?telegram_id=100").json()["matches"][0]["match_id"] == "other"
    login(client, 100)
    assert client.get("/api/me/best").json()["best"]["match_id"] == "2188356974"
    assert client.get(f"/api/me/screenshot-matches/{first['match']['screenshot_id']}").json()["match"]["source"] == "screenshot"
    assert client.get("/api/me/best?mode=duel").status_code == 422
    assert client.get("/api/me").json()["primary_uid"] is None


def test_bounded_gallery_keeps_all_database_records(db):
    for number in range(55):
        personal.save_screenshot_match(db, 100, board(str(number)))
    listing = personal.list_screenshot_matches(db, db.get(m.User, 100), "competitive")
    assert len(listing["matches"]) == 50 and listing["total"] == 55 and listing["truncated"]
    assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 55


@pytest.mark.parametrize("change", [
    {"telegram_id": 999}, {"image_bytes": "must-not-store"}, {"mode": "duel"},
    {"selected_index": -1}, {"image_fingerprint": "not-a-hash"},
])
def test_invalid_or_owner_image_fields_never_persist(db, change):
    payload = {**board(), **change}
    with pytest.raises(ValueError):
        personal.save_screenshot_match(db, 100, payload)
    assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 0


def test_context_score_and_place_are_not_rating_bonuses(db):
    payload = board("same")
    first = personal.save_screenshot_match(db, 100, payload)
    payload["rows"][0].update(game_score=999, rank=10)
    second = personal.save_screenshot_match(db, 100, payload)
    assert second["match"]["rating"] == first["match"]["rating"]
    assert second["best"]["players"][0]["game_score"] == 999
    assert not second["is_new_best"]


@pytest.mark.parametrize("mode", ["competitive", "allies"])
def test_sql_candidate_score_matches_decimal_formula_including_clamp_and_close_values(db, mode):
    cases = [(0, 0, 0), (0, 200, 0), (200, 0, 200), (16, 10, 3),
             (17, 11, 2), (9, 10, 10), (9, 10, 0), (15, 15, 4)]
    for index, (kills, deaths, assists) in enumerate(cases):
        saved = personal.save_screenshot_match(db, 100, board(str(index), mode=mode,
                                                kills=kills, deaths=deaths, assists=assists))
        computed = db.scalar(select(personal._rating_sql(m.ScreenshotMatch, mode)).where(
            m.ScreenshotMatch.id == saved["match"]["screenshot_id"]))
        expected = calculate(mode, kills, deaths, assists, rounds=None)["value"]
        assert Decimal(str(computed)).quantize(Decimal("0.00000001")) == expected


def test_empty_personal_views_work_without_source_or_primary_uid(client):
    login(client, 999)
    for mode in ("competitive", "allies"):
        assert client.get("/api/me/best", params={"mode": mode}).json() == {"best": None}
        assert client.get("/api/me/screenshot-matches", params={"mode": mode}).json()["matches"] == []


def test_winning_screenshot_correction_can_restore_automatic_record(db):
    user = personal.ensure_personal_user(db, 100)
    user.primary_uid = upsert_profile(db, profile()).uid
    ingest_page(db, user.primary_uid, "competitive", HistoryPage([match("automatic")]))
    payload = board("screenshot", kills=30, deaths=3, assists=10)
    assert personal.save_screenshot_match(db, 100, payload)["best"]["source"] == "screenshot"
    payload["rows"][0].update(kills=1, deaths=20, assists=0)
    corrected = personal.save_screenshot_match(db, 100, payload)
    assert corrected["best"]["source"] == "automatic" and corrected["best"]["match_id"] == "automatic"
    assert corrected["is_new_best"] is False


def test_concurrent_saves_keep_single_user_and_largest_record(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "concurrent.db"),
                           connect_args={"timeout": 15, "check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def save(number):
        with factory() as db:
            result = personal.save_screenshot_match(db, 100, board(str(number), kills=number + 10))
            db.commit()
            return result["match"]["rating"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        scores = list(executor.map(save, range(8)))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(m.User)) == 1
        best = personal.get_personal_best(db, db.get(m.User, 100), "competitive")
        assert best["rating"] == max(scores)
        assert db.scalar(select(func.count()).select_from(m.PersonalBest)) == 1
        assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 8
        db.commit()
    engine.dispose()
