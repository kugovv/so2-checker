from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    primary_uid: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("players.uid"))
    mode: Mapped[str] = mapped_column(String(20), default="competitive")
    screenshot_nickname: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_active: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Player(Base):
    __tablename__ = "players"
    uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True)
    nickname: Mapped[str] = mapped_column(String(128))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048))
    current_mmr: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), index=True)
    map: Mapped[Optional[str]] = mapped_column(String(128))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    score: Mapped[Optional[list]] = mapped_column(JSON)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("match_id", "player_uid", name="uq_participant_match_player"),
        Index("ix_participant_recent", "player_uid", "mode", "ended_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[str] = mapped_column(String(128), ForeignKey("matches.id", ondelete="CASCADE"))
    player_uid: Mapped[str] = mapped_column(String(32), ForeignKey("players.uid"))
    mode: Mapped[str] = mapped_column(String(20))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    team: Mapped[Optional[str]] = mapped_column(String(32))
    result: Mapped[Optional[str]] = mapped_column(String(12))
    kills: Mapped[Optional[int]] = mapped_column(Integer)
    deaths: Mapped[Optional[int]] = mapped_column(Integer)
    assists: Mapped[Optional[int]] = mapped_column(Integer)
    headshots: Mapped[Optional[int]] = mapped_column(Integer)
    rounds: Mapped[Optional[int]] = mapped_column(Integer)
    damage: Mapped[Optional[int]] = mapped_column(Integer)
    rank: Mapped[Optional[int]] = mapped_column(Integer)
    game_score: Mapped[Optional[int]] = mapped_column(Integer)
    mmr_before: Mapped[Optional[int]] = mapped_column(Integer)
    mmr_after: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[float]] = mapped_column(Numeric(14, 8))
    rating_tier: Mapped[Optional[str]] = mapped_column(String(32))
    formula_version: Mapped[str] = mapped_column(String(32))
    rating_inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    rating_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    rating_factors: Mapped[dict] = mapped_column(JSON, default=dict)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("player_uid", "mode", name="uq_observation_player_mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_uid: Mapped[str] = mapped_column(String(32), ForeignKey("players.uid"))
    mode: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    oldest_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    newest_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_match_ids: Mapped[list] = mapped_column(JSON, default=list)
    history_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    source_error: Mapped[Optional[str]] = mapped_column(Text)
    next_cursor: Mapped[Optional[str]] = mapped_column(String(256))


class HistoryGap(Base):
    __tablename__ = "history_gaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_uid: Mapped[str] = mapped_column(String(32), ForeignKey("players.uid"), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    after_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    before_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(256))


class RefreshJob(Base):
    __tablename__ = "refresh_jobs"
    __table_args__ = (UniqueConstraint("player_uid", "mode", name="uq_refresh_player_mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_uid: Mapped[str] = mapped_column(String(32), ForeignKey("players.uid"))
    mode: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)


class ScreenshotMatch(Base):
    __tablename__ = "screenshot_matches"
    __table_args__ = (
        UniqueConstraint("telegram_id", "mode", "dedup_key", name="uq_screenshot_owner_mode_key"),
        Index("ix_screenshot_owner_mode_created", "telegram_id", "mode", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(20))
    dedup_key: Mapped[str] = mapped_column(String(64))
    match_id: Mapped[Optional[str]] = mapped_column(String(128))
    nickname: Mapped[str] = mapped_column(String(80))
    kills: Mapped[int] = mapped_column(Integer)
    deaths: Mapped[int] = mapped_column(Integer)
    assists: Mapped[int] = mapped_column(Integer)
    rating: Mapped[float] = mapped_column(Numeric(14, 8))
    formula_version: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PersonalBest(Base):
    __tablename__ = "personal_bests"
    telegram_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    source_key: Mapped[str] = mapped_column(String(256))
    screenshot_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("screenshot_matches.id", ondelete="SET NULL"))
    rating: Mapped[float] = mapped_column(Numeric(14, 8))
    formula_version: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
