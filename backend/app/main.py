import asyncio
import secrets
import time
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from . import models as m
from .auth import InvalidInitData, session_hash, validate_init_data
from .config import get_settings
from .db import get_db
from .rating import VERSION, summarize
from .service import aware, match_card, queue_refresh, recent_rows, serialize_profile, upsert_profile
from .top import get_leaderboard
from .source import PlayerNotFound, SourceUnavailable, source

app = FastAPI(title="Зона 9 · Standoff Checker", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware,
                   allow_origins=[origin.strip() for origin in get_settings().allowed_origins.split(",") if origin.strip()],
                   allow_credentials=True, allow_methods=["GET", "POST", "PUT"],
                   allow_headers=["Authorization", "Content-Type"])
Mode = Literal["competitive", "allies"]
Scope = Literal["last20", "archive"]
_resolve_lock = asyncio.Lock()
_last_resolve = 0.0


@app.middleware("http")
async def request_security(request: Request, call_next):
    origin = request.headers.get("origin")
    configured = get_settings().public_url
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and configured:
        target = urlsplit(configured)
        if origin != f"{target.scheme}://{target.netloc}":
            return Response(status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class AuthBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    init_data: str = Field(max_length=16384)


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_uid: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    mode: Mode | None = None


def current_user(request: Request, db: Session = Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get("so2_session", "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Откройте приложение через Telegram-бота")
    session = db.get(m.AuthSession, session_hash(token))
    if session is None or aware(session.expires_at) <= m.utcnow():
        raise HTTPException(401, "Сессия истекла. Откройте приложение заново через бота.")
    user = db.get(m.User, session.telegram_id)
    if user is None:
        raise HTTPException(401, "Неизвестная сессия")
    return user


def validate_uid(uid):
    if not uid.isascii() or not uid.isdigit() or not 1 <= len(uid) <= 20:
        raise HTTPException(422, "Игровой ID должен содержать от 1 до 20 цифр")


def user_view(user):
    return {"primary_uid": user.primary_uid, "mode": user.mode}


@app.get("/api/status")
def status():
    return {"source_available": source.available, "source_message": source.message,
            "bot_username": get_settings().bot_username, "formula_version": VERSION}


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "База данных недоступна") from None
    return {"status": "ok", "database": "ok", "source_available": source.available}


@app.post("/api/auth")
def authenticate(body: AuthBody, response: Response, db: Session = Depends(get_db)):
    config = get_settings()
    try:
        identity = validate_init_data(body.init_data, config.bot_token, config.auth_max_age)
    except (ValueError, InvalidInitData):
        raise HTTPException(401, "Не удалось подтвердить вход. Откройте приложение заново через Telegram-бота.") from None
    user = db.get(m.User, identity["id"])
    if user is None:
        user = m.User(telegram_id=identity["id"])
        db.add(user)
        db.flush()
    user.last_active = m.utcnow()
    db.execute(delete(m.AuthSession).where(m.AuthSession.expires_at < m.utcnow()))
    # Reopening the Mini App replaces this user's sessions, bounding storage.
    db.execute(delete(m.AuthSession).where(m.AuthSession.telegram_id == user.telegram_id))
    token = secrets.token_urlsafe(32)
    db.add(m.AuthSession(token_hash=session_hash(token), telegram_id=user.telegram_id,
                         expires_at=m.utcnow() + timedelta(seconds=config.session_ttl)))
    db.commit()
    response.set_cookie("so2_session", token, max_age=config.session_ttl, secure=True, httponly=True, samesite="lax", path="/")
    # Opaque session for cross-origin Mini Apps; the frontend keeps it only in memory.
    return {**user_view(user), "session_token": token, "expires_in": config.session_ttl}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user_view(user)


async def resolve_player(uid, db):
    global _last_resolve
    validate_uid(uid)
    player = db.get(m.Player, uid)
    if player is not None:
        return player
    if not source.available:
        raise HTTPException(503, source.message)
    acquired = False
    try:
        # Bound unknown-ID lookups as well as history fetches. A single API process is intentional.
        await asyncio.wait_for(_resolve_lock.acquire(), timeout=2)
        acquired = True
        db.expire_all()
        player = db.get(m.Player, uid)
        if player is not None:
            return player
        delay = get_settings().source_min_interval - (time.monotonic() - _last_resolve)
        if delay > 0:
            await asyncio.sleep(delay)
        _last_resolve = time.monotonic()
        profile = await asyncio.wait_for(source.resolve_uid(uid), timeout=get_settings().source_timeout)
        if profile.uid != uid:
            raise SourceUnavailable("Источник вернул другой игровой ID")
        player = upsert_profile(db, profile)
        db.commit()
        return player
    except PlayerNotFound:
        raise HTTPException(404, "Игрок с таким ID не найден") from None
    except (SourceUnavailable, TimeoutError):
        raise HTTPException(503, "Источник статистики временно недоступен. Попробуйте позже.") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Не удалось получить подтверждённые данные игрока. Попробуйте позже.") from None
    finally:
        if acquired:
            _resolve_lock.release()


@app.put("/api/me")
async def update_me(body: SettingsBody, user=Depends(current_user), db: Session = Depends(get_db)):
    if body.primary_uid is not None:
        player = await resolve_player(body.primary_uid, db)
        user.primary_uid = player.uid
        if source.available:
            queue_refresh(db, player.uid, body.mode or user.mode)
    if body.mode is not None:
        user.mode = body.mode
    db.commit()
    return user_view(user)


@app.get("/api/players/{uid}")
async def player_profile(uid: str, mode: Mode = "competitive", scope: Scope = "last20", user=Depends(current_user), db: Session = Depends(get_db)):
    player = await resolve_player(uid, db)
    if source.available:
        queue_refresh(db, uid, mode)
        db.commit()
    return serialize_profile(db, player, mode, scope)


@app.post("/api/players/{uid}/refresh")
async def refresh(uid: str, mode: Mode = "competitive", user=Depends(current_user), db: Session = Depends(get_db)):
    player = await resolve_player(uid, db)
    if not source.available:
        raise HTTPException(503, source.message)
    # Respect the same cache TTL even for explicit refreshes to avoid source flooding.
    job = queue_refresh(db, uid, mode)
    db.commit()
    return {"state": job.state if job else "cached", "job_id": job.id if job else None}


@app.get("/api/matches/{match_id}")
def match_detail(match_id: str, uid: str, user=Depends(current_user), db: Session = Depends(get_db)):
    validate_uid(uid)
    participant = db.scalar(select(m.Participant).where(m.Participant.match_id == match_id, m.Participant.player_uid == uid))
    if participant is None:
        raise HTTPException(404, "Матч не найден в сохранённой истории игрока")
    rows = db.scalars(select(m.Participant).where(m.Participant.match_id == match_id).order_by(m.Participant.team, m.Participant.rank))
    participants = []
    for row in rows:
        player = db.get(m.Player, row.player_uid)
        participants.append({"uid": player.uid, "nickname": player.nickname, "team": row.team,
                             **match_card(db, row), "rank": row.rank, "game_score": row.game_score,
                             "adr": row.damage / row.rounds if row.damage is not None and row.rounds else None})
    return {"match": match_card(db, participant), "participants": participants,
            "explanation": {"version": participant.formula_version, "tier": participant.rating_tier,
                            "inputs": participant.rating_inputs, "parameters": participant.rating_parameters,
                            "factors": participant.rating_factors}}


@app.get("/api/top")
def leaderboard(mode: Mode = "competitive", user=Depends(current_user), db: Session = Depends(get_db)):
    return get_leaderboard(db, mode)
