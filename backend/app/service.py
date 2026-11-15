import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings
from .rating import VERSION, formula_version, calculate, displayed, rating_color, summarize
from .source import HistoryPage, SourceMatch, SourceProfile, SourceUnavailable

MODES = {"competitive", "allies"}


def aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def iso(value):
    return aware(value).isoformat() if value else None


def upsert_profile(db: Session, profile: SourceProfile):
    player = db.get(m.Player, profile.uid)
    if player and player.source_id != profile.source_id:
        raise SourceUnavailable("Источник вернул противоречивое соответствие игрового ID")
    if not player:
        player = m.Player(uid=profile.uid, source_id=profile.source_id, nickname=profile.nickname)
        db.add(player)
    player.nickname = profile.nickname
    player.avatar_url = profile.avatar_url if profile.avatar_url and profile.avatar_url.startswith("https://") else None
    player.current_mmr = profile.current_mmr
    player.observed_at = m.utcnow()
    db.flush()
    return player


def observation(db, uid, mode):
    result = db.scalar(select(m.Observation).where(m.Observation.player_uid == uid, m.Observation.mode == mode))
    if result is None:
        result = m.Observation(player_uid=uid, mode=mode, last_match_ids=[])
        db.add(result)
        db.flush()
    return result


def ingest_page(db: Session, uid: str, mode: str, page: HistoryPage, first_page: bool = True):
    """A whole observation commits atomically. PKs and unique constraints deduplicate."""
    obs = observation(db, uid, mode)
    accepted = []
    for match in page.matches:
        if match.mode != mode or mode not in MODES or not match.completed:
            continue
        if not any(p.profile.uid == uid for p in match.participants):
            continue
        if not match.id or match.ended_at is None:
            continue
        existing = db.get(m.Match, match.id)
        if existing and existing.mode != mode:
            raise SourceUnavailable("Источник вернул конфликт режимов матча")
        if not existing:
            existing = m.Match(id=match.id, mode=mode, map=match.map, ended_at=match.ended_at, score=match.score, completed=True)
            db.add(existing)
            db.flush()
        for person in match.participants:
            upsert_profile(db, person.profile)
            stats = {key: getattr(person, key) for key in ("kills", "deaths", "assists", "headshots", "rounds", "damage", "rank", "game_score", "mmr_before", "mmr_after")}
            if any(value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0) for value in stats.values()):
                raise SourceUnavailable("Источник вернул некорректные показатели матча")
            if person.headshots is not None and person.kills is not None and person.headshots > person.kills:
                raise SourceUnavailable("Источник вернул некорректные убийства в голову")
            rating = calculate(mode, person.kills, person.deaths, person.assists, person.rounds)
            participant = db.scalar(select(m.Participant).where(m.Participant.match_id == match.id, m.Participant.player_uid == person.profile.uid))
            if participant is None:
                participant = m.Participant(match_id=match.id, player_uid=person.profile.uid, mode=mode, ended_at=match.ended_at, formula_version=formula_version(mode))
                db.add(participant)
            for key, value in stats.items():
                setattr(participant, key, value)
            participant.team = person.team
            participant.result = person.result
            participant.rating = rating["value"]
            participant.rating_tier = rating["tier"]
            participant.formula_version = formula_version(mode)
            participant.rating_inputs = rating["inputs"]
            participant.rating_parameters = rating["parameters"]
            participant.rating_factors = rating["factors"]
            db.flush()
        accepted.append(match)
    if first_page:
        ids = {item.id for item in accepted}
        previous_ids = set(obs.last_match_ids or [])
        # A disjoint latest window signals a potential gap, not a proven number of missing games.
        if ids and previous_ids and not ids.intersection(previous_ids):
            oldest = min(aware(item.ended_at) for item in accepted)
            if obs.newest_seen and oldest > aware(obs.newest_seen):
                db.add(m.HistoryGap(player_uid=uid, mode=mode, after_at=obs.newest_seen, before_at=oldest,
                                    reason="Новое окно матчей не пересекается с предыдущим. Между наблюдениями возможны недоступные игры."))
        obs.last_match_ids = [item.id for item in accepted]
    times = [aware(item.ended_at) for item in accepted]
    if times:
        obs.oldest_seen = min(times + ([aware(obs.oldest_seen)] if obs.oldest_seen else []))
        obs.newest_seen = max(times + ([aware(obs.newest_seen)] if obs.newest_seen else []))
    obs.next_cursor = page.next_cursor
    obs.history_complete = page.history_complete
    obs.updated_at = m.utcnow()
    obs.source_error = None
    db.flush()
    return len(accepted)


def queue_refresh(db: Session, uid: str, mode: str, force=False):
    job = db.scalar(select(m.RefreshJob).where(m.RefreshJob.player_uid == uid, m.RefreshJob.mode == mode))
    if job and job.state in {"pending", "running"}:
        return job
    obs = db.scalar(select(m.Observation).where(m.Observation.player_uid == uid, m.Observation.mode == mode))
    if not force and obs and obs.updated_at and (m.utcnow() - aware(obs.updated_at)).total_seconds() < get_settings().refresh_ttl:
        return job
    if job is None:
        job = m.RefreshJob(player_uid=uid, mode=mode)
        try:
            with db.begin_nested():
                db.add(job)
                db.flush()
        except IntegrityError:
            return db.scalar(select(m.RefreshJob).where(m.RefreshJob.player_uid == uid, m.RefreshJob.mode == mode))
    else:
        job.state = "pending"
        job.attempts = 0
        job.requested_at = m.utcnow()
        job.error = None
    db.flush()
    return job


def recent_rows(db: Session, uid: str, mode: str, limit=20):
    query = select(m.Participant).join(m.Match, m.Participant.match_id == m.Match.id).where(
        m.Participant.player_uid == uid, m.Participant.mode == mode, m.Match.completed.is_(True)
    ).order_by(m.Participant.ended_at.desc(), m.Participant.match_id.desc())
    return list(db.scalars(query.limit(limit) if limit else query))


def match_card(db, row):
    match = db.get(m.Match, row.match_id)
    return {
        "id": match.id, "map": match.map, "ended_at": iso(match.ended_at), "mode": match.mode,
        "score": match.score, "result": row.result,
        **{key: getattr(row, key) for key in ("kills", "deaths", "assists", "headshots", "rounds", "mmr_before", "mmr_after")},
        "rating": float(row.rating) if row.rating is not None else None,
        "rating_display": displayed(row.rating), "rating_color": rating_color(row.rating), "tier": row.rating_tier,
        "formula_version": row.formula_version,
    }


def serialize_profile(db, player, mode, scope):
    # The headline always stays the latest twenty, regardless of selected archive distance.
    latest = recent_rows(db, player.uid, mode)
    rows = recent_rows(db, player.uid, mode, limit=10000) if scope == "archive" else latest
    obs = db.scalar(select(m.Observation).where(m.Observation.player_uid == player.uid, m.Observation.mode == mode))
    job = db.scalar(select(m.RefreshJob).where(m.RefreshJob.player_uid == player.uid, m.RefreshJob.mode == mode))
    gaps = db.scalars(select(m.HistoryGap).where(m.HistoryGap.player_uid == player.uid, m.HistoryGap.mode == mode).order_by(m.HistoryGap.detected_at.desc()))
    return {
        "player": {"uid": player.uid, "nickname": player.nickname, "avatar_url": player.avatar_url,
                   "current_mmr": {key: player.current_mmr.get(key) for key in MODES}},
        "mode": mode, "scope": scope, "summary": summarize(latest),
        "matches": [match_card(db, row) for row in rows],
        "updated_at": iso(obs.updated_at) if obs else None,
        "refresh_state": job.state if job else "idle",
        "source_error": obs.source_error if obs else None,
        "history_complete": bool(obs and obs.history_complete),
        "archive_truncated": scope == "archive" and len(rows) == 10000,
        "gaps": [{"detected_at": iso(g.detected_at), "after_at": iso(g.after_at), "before_at": iso(g.before_at), "reason": g.reason} for g in gaps],
    }
