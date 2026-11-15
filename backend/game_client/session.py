"""Private ticket storage. An email/password is not a game session ticket."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Protocol

from .errors import SessionUnavailable, UnsupportedAuthentication

MAX_TICKET_FILE = 64 * 1024


@dataclass(frozen=True)
class TicketSession:
    ticket: str = field(repr=False)
    expires_at: float | None = None

    def validate(self, now: float | None = None) -> None:
        if not isinstance(self.ticket, str) or not self.ticket or len(self.ticket.encode()) > 32 * 1024:
            raise SessionUnavailable()
        if self.ticket != self.ticket.strip() or any(ord(c) < 32 for c in self.ticket):
            raise SessionUnavailable()
        if self.expires_at is not None:
            if not isinstance(self.expires_at, (int, float)) or not self.expires_at > (time.time() if now is None else now):
                raise SessionUnavailable()


class TicketProvider(Protocol):
    """A provider must obtain a legitimate server-issued ticket, never invent one."""

    async def get_ticket(self) -> TicketSession: ...


class AuthCodeProvider(Protocol):
    """Provisioning seam only; SDK AppVerification is not implemented or bypassed.

    No RPC currently consumes this protocol. Implementing a real authentication
    exchange requires an official code and verification data, not a password.
    """

    async def get_authorization_code(self) -> str: ...


def _read_private(path: Path) -> bytes:
    descriptor = None
    try:
        if path.is_symlink():
            raise SessionUnavailable()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TICKET_FILE:
            raise SessionUnavailable()
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise SessionUnavailable()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_TICKET_FILE + 1)
        if not data or len(data) > MAX_TICKET_FILE:
            raise SessionUnavailable()
        return data
    except (OSError, ValueError):
        raise SessionUnavailable() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class FileTicketProvider:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __repr__(self):
        return "FileTicketProvider(<private>)"

    async def get_ticket(self) -> TicketSession:
        try:
            data = _read_private(self.path).decode("utf-8").strip()
            if data.startswith("{"):
                payload = json.loads(data)
                session = TicketSession(payload["ticket"], payload.get("expires_at"))
            else:
                session = TicketSession(data)
            session.validate()
            return session
        except (UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            raise SessionUnavailable() from None


@dataclass(frozen=True)
class LoadedTicketProvider:
    session: TicketSession = field(repr=False)

    async def get_ticket(self) -> TicketSession:
        self.session.validate()
        return self.session


def store_ticket(path: str | Path, session: TicketSession) -> None:
    """Atomically persist an already-issued ticket with owner-only POSIX access."""
    session.validate()
    destination = Path(path)
    temporary = None
    try:
        if destination.is_symlink():
            raise SessionUnavailable()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".game-session-",
                                         dir=destination.parent, delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            json.dump({"ticket": session.ticket, "expires_at": session.expires_at}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    except (OSError, ValueError, TypeError):
        raise SessionUnavailable() from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


async def password_login(email: str, password: str) -> TicketSession:
    """Explicit refusal: neither password nor fabricated authCode reaches the game."""
    raise UnsupportedAuthentication()
