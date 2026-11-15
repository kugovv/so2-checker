import time
from datetime import timedelta

from sqlalchemy import select

from app import models as m
from app.auth import InvalidInitData, session_hash, validate_init_data
from app.config import get_settings
from app.service import ingest_page, upsert_profile
from app.source import HistoryPage, SourceUnavailable
from tests.helpers import FakeSource, login, match, profile, signed_data


def test_valid_signature_and_other_bot_rejected():
    import pytest
    raw = signed_data(now=1000)
    assert validate_init_data(raw, get_settings().bot_token, now=1001)["id"] == 100
    with pytest.raises(InvalidInitData):
        validate_init_data(raw, "another-bot-token", now=1001)


def test_forged_expired_future_duplicate_rejected(client):
    for raw in (signed_data() + "&user=%7B%22id%22%3A999%7D", signed_data(age=301), signed_data(age=-60), signed_data(token="wrong-token")):
        assert client.post("/api/auth", json={"init_data": raw}).status_code == 401
    assert client.post("/api/auth", json={"init_data": "fake"}).status_code == 401
    assert client.get("/api/me?telegram_id=100").status_code == 401


def test_session_is_hashed_expiring_cookie_and_bearer(client, db_factory):
    response = login(client)
    token = response.json()["session_token"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    with db_factory() as db:
        session = db.get(m.AuthSession, session_hash(token))
        assert session is not None and session.token_hash != token
        session.expires_at = m.utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_settings_owned_by_verified_user_and_search_does_not_rebind(client, db_factory, monkeypatch):
    import app.main as main
    fake = FakeSource()
    monkeypatch.setattr(main, "source", fake)
    login(client, 100)
    assert client.put("/api/me", json={"primary_uid": "12345678", "mode": "allies"}).status_code == 200
    assert client.get("/api/players/98765432?mode=allies").status_code == 200
    assert client.get("/api/me").json() == {"primary_uid": "12345678", "mode": "allies"}
    assert client.put("/api/me", json={"telegram_id": 999, "primary_uid": "98765432"}).status_code == 422
    login(client, 200)
    assert client.get("/api/me").json()["primary_uid"] is None
    client.put("/api/me", json={"primary_uid": "98765432"})
    with db_factory() as db:
        assert db.get(m.User, 100).primary_uid == "12345678"
        assert db.get(m.User, 200).primary_uid == "98765432"


def test_unconfigured_source_cannot_bind_or_fake_profile(client, db_factory):
    login(client)
    response = client.put("/api/me", json={"primary_uid": "12345678"})
    assert response.status_code == 503
    assert client.get("/api/me").json()["primary_uid"] is None
    assert client.get("/api/players/12345678").status_code == 503
    assert client.get("/api/status").json()["source_available"] is False


def test_top_endpoint_removed_for_authenticated_and_anonymous_users(client):
    assert client.get("/api/top").status_code == 404
    login(client)
    for mode in ("competitive", "allies"):
        assert client.get("/api/top", params={"mode": mode}).status_code == 404


def test_cached_profile_read_no_source_query_and_errors_keep_data(client, db_factory, monkeypatch):
    import app.main as main
    with db_factory() as db:
        upsert_profile(db, profile())
        ingest_page(db, "12345678", "competitive", HistoryPage([match()]))
        db.commit()
    login(client)
    fake = FakeSource()
    monkeypatch.setattr(main, "source", fake)
    response = client.get("/api/players/12345678")
    assert response.status_code == 200
    assert response.json()["summary"]["count"] == 1
    assert fake.calls == 0
    assert response.json()["updated_at"] is not None
    assert response.json()["refresh_state"] == "idle"


def test_modes_independent_detail_and_no_duel(client, db_factory):
    with db_factory() as db:
        upsert_profile(db, profile())
        ingest_page(db, "12345678", "competitive", HistoryPage([match()]))
        ingest_page(db, "12345678", "allies", HistoryPage([match("m2", mode="allies", kills=3, headshots=1)]))
        db.commit()
    login(client)
    comp = client.get("/api/players/12345678?mode=competitive").json()
    allies = client.get("/api/players/12345678?mode=allies").json()
    assert comp["summary"]["avg"] == 15 and allies["summary"]["avg"] == 3
    assert client.get("/api/players/12345678?mode=duel").status_code == 422
    detail = client.get("/api/matches/m1?uid=12345678").json()
    assert detail["explanation"]["inputs"]["kills"] == 15
    assert detail["participants"][0]["uid"] == "12345678"


def test_cors_only_configured_frontend(client):
    allowed = client.options("/api/me", headers={"Origin": "https://frontend.example.test", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Authorization"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://frontend.example.test"
    rejected = client.options("/api/me", headers={"Origin": "https://attacker.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in rejected.headers
    assert client.put("/api/me", json={"mode": "allies"}, headers={"Origin": "https://attacker.test"}).status_code == 403
