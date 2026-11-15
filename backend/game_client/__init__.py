"""Server-side, read-only game protocol client. Not a password login service."""

from .client import GameClient, ProtocolProfile
from .errors import GameClientError
from .session import FileTicketProvider, TicketSession

__all__ = ["GameClient", "GameClientError", "ProtocolProfile", "FileTicketProvider", "TicketSession"]
