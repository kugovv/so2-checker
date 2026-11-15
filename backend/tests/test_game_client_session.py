import asyncio
import json
import os
import time

import pytest

from game_client.__main__ import arguments, run
from game_client.errors import SessionUnavailable, UnsupportedAuthentication
from game_client.session import FileTicketProvider, TicketSession, password_login, store_ticket


def test_private_ticket_roundtrip_and_no_secret_repr(tmp_path):
    private = tmp_path / "ticket.json"
    session = TicketSession("secret-ticket-never-log", time.time() + 1000)
    store_ticket(private, session)
    loaded = asyncio.run(FileTicketProvider(private).get_ticket())
    assert loaded == session
    assert "secret-ticket-never-log" not in repr(session)
    assert "ticket.json" not in repr(FileTicketProvider(private))
    if os.name == "posix":
        assert private.stat().st_mode & 0o777 == 0o600
        private.chmod(0o644)
        with pytest.raises(SessionUnavailable):
            asyncio.run(FileTicketProvider(private).get_ticket())


@pytest.mark.parametrize("payload", [b"", b"{invalid-json-secret", b"\xff", b"x" * 65537,
                                     b'{"ticket":"secret","expires_at":1}', b'{"ticket":null}'],
                         ids=["empty", "invalid-json", "invalid-utf8", "oversized", "expired", "null-ticket"])
def test_missing_malformed_or_expired_ticket_is_sanitized(tmp_path, payload):
    private = tmp_path / "ticket"
    private.write_bytes(payload)
    private.chmod(0o600)
    with pytest.raises(SessionUnavailable) as caught:
        asyncio.run(FileTicketProvider(private).get_ticket())
    assert str(caught.value) == "game_session_unavailable"


def test_plain_ticket_supported_and_newlines_inside_ticket_rejected(tmp_path):
    private = tmp_path / "ticket"
    private.write_text("legitimate-issued-ticket\n")
    private.chmod(0o600)
    assert asyncio.run(FileTicketProvider(private).get_ticket()).ticket == "legitimate-issued-ticket"
    private.write_text("first-part\nsecond-part")
    with pytest.raises(SessionUnavailable):
        asyncio.run(FileTicketProvider(private).get_ticket())


def test_password_login_is_never_fabricated():
    with pytest.raises(UnsupportedAuthentication) as caught:
        asyncio.run(password_login("private@example.test", "do-not-send-this-password"))
    assert "private@example.test" not in str(caught.value)
    assert "do-not-send" not in repr(caught.value)


def test_cli_rejects_missing_ticket_and_invalid_uid_before_connecting(tmp_path, monkeypatch):
    def must_not_construct(**kwargs):
        raise AssertionError("local validation must precede network connection")
    monkeypatch.setattr("game_client.__main__.GameClient", must_not_construct)
    args = arguments(["--ticket-file", str(tmp_path / "missing"), "--uid", "202777998"])
    report, code = asyncio.run(run(args))
    assert code == 1 and report["error"] == "game_session_unavailable"
    assert report["tls_verified"] is False
    private = tmp_path / "valid-ticket"
    store_ticket(private, TicketSession("legitimate-ticket"))
    args = arguments(["--ticket-file", str(private), "--uid", "not-a-public-uid"])
    report, code = asyncio.run(run(args))
    assert code == 1 and report["error"] == "invalid_local_configuration"
    assert report["tls_verified"] is False


def test_cli_preflight_cannot_accidentally_receive_a_ticket():
    with pytest.raises(SystemExit):
        arguments(["--preflight", "--ticket-file", "/private/ticket", "--uid", "202777998"])
    with pytest.raises(SystemExit):
        arguments(["--uid", "202777998"])
    with pytest.raises(SystemExit):
        arguments(["--transport-only", "--profile", "legacy-aes"])
