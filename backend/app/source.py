"""Boundary for an authenticated, verified data provider. Never fabricates statistics."""
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from typing import Protocol

from .config import get_settings


class SourceUnavailable(Exception):
    pass


class PlayerNotFound(Exception):
    pass


@dataclass
class SourceProfile:
    uid: str
    source_id: str
    nickname: str
    avatar_url: str | None = None
    current_mmr: dict = field(default_factory=dict)


@dataclass
class SourceParticipant:
    profile: SourceProfile
    team: str | None = None
    result: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    headshots: int | None = None
    rounds: int | None = None  # Explicitly player rounds, never copied from team score.
    damage: int | None = None
    rank: int | None = None
    game_score: int | None = None
    mmr_before: int | None = None
    mmr_after: int | None = None


@dataclass
class SourceMatch:
    id: str
    mode: str
    ended_at: datetime
    participants: list[SourceParticipant]
    map: str | None = None
    score: list[int] | None = None
    completed: bool = True


@dataclass
class HistoryPage:
    matches: list[SourceMatch]
    next_cursor: str | None = None
    # Only true if provider explicitly proves an account-complete archive.
    history_complete: bool = False


class Source(Protocol):
    available: bool
    message: str
    supports_pagination: bool

    async def resolve_uid(self, uid: str) -> SourceProfile: ...
    async def fetch_history(self, profile: SourceProfile, mode: str, cursor: str | None = None) -> HistoryPage: ...


class UnconfiguredSource:
    available = False
    supports_pagination = False
    message = "Источник Standoff 2 ещё не подключён. Нужен авторизованный игровой доступ; статистика не подменяется демонстрационными данными."

    async def resolve_uid(self, uid: str) -> SourceProfile:
        raise SourceUnavailable(self.message)

    async def fetch_history(self, profile: SourceProfile, mode: str, cursor: str | None = None) -> HistoryPage:
        raise SourceUnavailable(self.message)


def load_source() -> Source:
    adapter = get_settings().source_adapter
    if not adapter:
        return UnconfiguredSource()
    module, factory = adapter.rsplit(":", 1)
    return getattr(import_module(module), factory)()


source = load_source()
