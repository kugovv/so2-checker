import asyncio

import pytest

from game_client.client import ResolvedPlayer
from game_client.diagnostics import FieldReport, game_snapshot, private_json
from game_client.errors import PaginationError, ProtocolError
from game_client.history import collect_history
from game_client.schemas import BOLT, message


def player():
    raw = message(BOLT + "Player", gpid="opaque-internal", uid="202777998", name="Проверка")
    return ResolvedPlayer(raw.uid, raw.gpid, raw)


def match(index, **overrides):
    values = {"matchId": str(index), "startDate": 100000 - index * 10,
              "finishDate": 100009 - index * 10,
              "players": [{"gpid": "opaque-internal", "uid": "202777998"}]}
    values.update(overrides)
    return message(BOLT + "matches.FinishedMatch", **values)


def test_history_collects_50_unique_finished_matches_and_advances_raw_offset():
    async def check():
        records = [match(0), match(0), match(1, state=1), match(2, state=2)] + [match(i) for i in range(3, 65)]
        offsets = []
        async def page(owner, *, offset, length):
            assert owner.gpid == "opaque-internal"
            offsets.append(offset)
            return records[offset:offset + length]
        batch = await collect_history(page, player())
        assert len(batch.matches) == 50
        assert len({m.matchId for m in batch.matches}) == 50
        assert offsets == [0, 20, 40]
        assert batch.excluded == {"duplicate": 1, "canceled_or_annulled": 2}
        assert batch.stop_reason == "requested_limit"
        assert batch.source_records_beyond_20
        assert not batch.mode_mapping_verified
        assert not batch.complete_account_history
        assert all(m.state == 0 for m in batch.matches)
    asyncio.run(check())


def test_repeated_server_window_does_not_invent_deeper_history():
    async def check():
        async def page(owner, *, offset, length):
            return [match(i) for i in range(20)]
        batch = await collect_history(page, player())
        assert len(batch.matches) == 20
        assert batch.pages == 2
        assert batch.stop_reason == "no_new_match_ids"
        assert not batch.source_records_beyond_20
        assert not batch.complete_account_history
    asyncio.run(check())


def test_short_page_does_not_assume_a_cap_and_history_gaps_remain_explicit():
    async def check():
        offsets = []
        async def page(owner, *, offset, length):
            offsets.append(offset)
            return [match(offset)] if offset < 3 else []
        batch = await collect_history(page, player(), previous_match_ids=frozenset({"older-unseen"}))
        assert offsets == [0, 1, 2, 3]
        assert len(batch.matches) == 3 and batch.possible_gap
        assert batch.overlap_found is False and batch.stop_reason == "empty_page"
        overlap = await collect_history(page, player(), previous_match_ids=frozenset({"2"}))
        assert overlap.overlap_found is True and not overlap.possible_gap
    asyncio.run(check())


def test_malformed_identity_and_dates_are_excluded_without_statistic_guessing():
    async def check():
        records = [match(0, matchId=""), match(1, finishDate=0), match(2, players=[]),
                   match(3, players=[{"gpid": "opaque-internal", "uid": "wrong"}]),
                   match(4, matchType=1), match(5)]
        async def page(owner, *, offset, length):
            return records[offset:offset + length]
        batch = await collect_history(page, player())
        assert [item.matchId for item in batch.matches] == ["4", "5"]
        assert batch.excluded["missing_match_id"] == 1
        assert batch.excluded["invalid_finished_date"] == 1
        assert batch.excluded["target_participant_missing_or_inconsistent"] == 2
        # matchType=CLAN_BATTLE is preserved, never interpreted as 2x2/5x5.
        assert batch.matches[0].matchType == 1
        assert batch.mode_mapping_verified is False
        assert not batch.matches[0].properties
    asyncio.run(check())


def test_page_limit_and_oversized_pages_are_bounded():
    async def check():
        async def page(owner, *, offset, length):
            return [match(i) for i in range(offset, offset + length)]
        batch = await collect_history(page, player(), max_pages=1)
        assert len(batch.matches) == 20 and batch.stop_reason == "page_limit"
        with pytest.raises(ValueError):
            await collect_history(page, player(), max_matches=51)
        async def invalid(owner, *, offset, length):
            return [match(i) for i in range(length + 1)]
        with pytest.raises(PaginationError):
            await collect_history(invalid, player())
    asyncio.run(check())


def test_retained_history_has_total_byte_budget_and_conflicting_duplicates_are_excluded(monkeypatch):
    monkeypatch.setattr("game_client.history.MAX_HISTORY_BYTES", 600)
    async def check():
        async def page(owner, *, offset, length):
            return [match(i, properties=[{"name": "large", "stringValue": "x" * 100}])
                    for i in range(offset, offset + length)]
        with pytest.raises(PaginationError):
            await collect_history(page, player())
        async def conflicting(owner, *, offset, length):
            return [] if offset else [match(0, players=[
                {"gpid": "opaque-internal", "uid": "202777998"},
                {"gpid": "different-internal", "uid": "202777998"}]), match(1, matchId="x" * 257)]
        batch = await collect_history(conflicting, player())
        assert not batch.matches
        assert batch.excluded["target_participant_missing_or_inconsistent"] == 1
        assert batch.excluded["oversized_match_id"] == 1
    asyncio.run(check())


def test_schema_diagnostics_do_not_emit_dynamic_values_or_synthetic_zeroes(tmp_path):
    report = FieldReport()
    raw = match(0, properties=[{"name": "unknown-kills-property", "intValue": 913762}])
    report.observe(raw, "match")
    summary = report.summary()
    assert summary["statistic_mapping_verified"] is False
    assert summary["dynamic_names_observed"]["match.properties"]["unknown-kills-property"] == 1
    assert summary["fields"]["match.players.name"]["present_nondefault"] == 0
    assert "913762" not in str(summary)
    assert "absent is not measured zero" in summary["presence_note"]
    destination = tmp_path / "schema.json"
    private_json(destination, summary)
    assert destination.exists()
    with pytest.raises(ProtocolError):
        private_json(destination, {"too_big": "x" * (3 * 1024 * 1024)})
