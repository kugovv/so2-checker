"""Private screenshot archive and one best K/D/A match per Telegram user/mode.

Screenshot rows never enter canonical matches, last-20 summaries or public player
profiles. All record candidates use the same preliminary per-match K/D/A method;
ordinary automatic-history ratings retain their original calculation.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import unicodedata

from sqlalchemy import Numeric, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import models as m
from .rating import ALLIES_VERSION, ALLIES_PARAMETERS, PARAMETERS, VERSION, WEIGHTS, calculate_match as calculate, match_round_count, displayed, rating_color

MODES = {"competitive", "allies"}
METHOD_NOTE = "Единый расчёт по K/D/A"
SCREENSHOT_NOTE = "Подтверждена ваша строка; остальные показатели распознаны со скриншота."
PAYLOAD_FIELDS = {"mode", "rows", "selected_index", "requested_nickname", "match_id", "map_name",
                  "score", "played_at", "date_label", "image_fingerprint", "personal_rounds"}
ROW_FIELDS = {"nickname", "aliases", "uid", "team", "kills", "deaths", "assists", "game_score", "rank"}


def _version(mode):
    return ALLIES_VERSION if mode == "allies" else VERSION


def _insert(db, model):
    name = db.get_bind().dialect.name
    if name == "postgresql":
        return pg_insert(model)
    if name == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError("Personal records require PostgreSQL or SQLite")


def ensure_personal_user(db, telegram_id, *, lock=False):
    """Use only a server-verified Telegram ID or the trusted bot update identity."""
    if type(telegram_id) is not int or not 0 < telegram_id < 2 ** 63:
        raise ValueError("Некорректный Telegram-пользователь")
    now = m.utcnow()
    statement = _insert(db, m.User).values(telegram_id=telegram_id, mode="competitive",
                                          created_at=now, last_active=now)
    db.execute(statement.on_conflict_do_nothing(index_elements=[m.User.telegram_id]))
    query = select(m.User).where(m.User.telegram_id == telegram_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    return db.scalar(query)


def _mode(mode):
    if mode not in MODES:
        raise ValueError("Поддерживаются только 5×5 и 2×2")
    return mode


def _text(value, maximum, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError("Некорректные текстовые данные скриншота")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Некорректные текстовые данные скриншота")
    return value


def _nickname_key(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _stat(value, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value <= 200:
        raise ValueError("Показатели должны быть целыми числами от 0 до 200")
    return value


def _date(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Некорректная дата матча") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Дата матча должна содержать часовой пояс")
    return value.astimezone(timezone.utc).isoformat()


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _normalize_payload(payload):
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_FIELDS:
        raise ValueError("Разрешены только показатели и метаданные матча")
    mode = _mode(payload.get("mode"))
    source_rows = payload.get("rows")
    selected = payload.get("selected_index")
    if (not isinstance(source_rows, list) or not 1 <= len(source_rows) <= 10
            or type(selected) is not int or not 0 <= selected < len(source_rows)):
        raise ValueError("Выберите свою строку исходной таблицы")
    rows = []
    for index, item in enumerate(source_rows):
        if not isinstance(item, dict) or set(item) - ROW_FIELDS:
            raise ValueError("Некорректная строка таблицы")
        nickname = _text(item.get("nickname"), 80)
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or len(aliases) > 3:
            raise ValueError("Некорректные варианты никнейма")
        aliases = list(dict.fromkeys(_text(alias, 80) for alias in aliases))
        uid = _text(item.get("uid"), 20, optional=True)
        if uid is not None and not re.fullmatch(r"[0-9]{1,20}", uid, re.ASCII):
            raise ValueError("Некорректный игровой ID")
        rows.append({"nickname": nickname, "aliases": aliases, "uid": uid,
                     "team": _text(item.get("team"), 32, optional=True),
                     **{field: _stat(item.get(field), optional=index != selected)
                        for field in ("kills", "deaths", "assists")}})
        for field, minimum, maximum in (("game_score", 0, 999), ("rank", 1, 10)):
            value = item.get(field)
            if value is not None and (type(value) is not int or not minimum <= value <= maximum):
                raise ValueError("Некорректные контекстные показатели таблицы")
            rows[-1][field] = value
    score = payload.get("score")
    if score is not None:
        if (not isinstance(score, (list, tuple)) or len(score) != 2
                or any(type(value) is not int or not 0 <= value <= 999 for value in score)):
            raise ValueError("Некорректный итоговый счёт")
        score = list(score)
    match_id = _text(payload.get("match_id"), 128, optional=True)
    if match_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", match_id, re.ASCII):
        raise ValueError("Некорректный номер матча")
    fingerprint = payload.get("image_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint)):
        raise ValueError("Некорректный отпечаток скриншота")
    personal_rounds = payload.get('personal_rounds')
    if personal_rounds is not None:
        if (mode != 'competitive' or type(personal_rounds) is not int or not 1 <= personal_rounds <= 200
                or not score or personal_rounds > sum(score) or rows[selected]['deaths'] > personal_rounds):
            raise ValueError('Некорректное подтверждение личных раундов')
    return {"mode": mode, "rows": rows, "selected_index": selected,
            "requested_nickname": _text(payload.get("requested_nickname"), 80, optional=True),
            "match_id": match_id, "map_name": _text(payload.get("map_name"), 128, optional=True),
            "score": score, "played_at": _date(payload.get("played_at")),
            "date_label": _text(payload.get("date_label"), 80, optional=True),
            "image_fingerprint": fingerprint.lower() if fingerprint else None, "personal_rounds": personal_rounds}


def _dedup_key(payload):
    """Without a match ID/image hash, metadata/roster identity is necessarily limited.

    Exclude the selected K/D/A so a correction updates the same uploaded game.
    No image bytes, paths, Telegram file IDs or URLs enter the database.
    """
    own = payload["rows"][payload["selected_index"]]
    identity = _nickname_key(payload["requested_nickname"] or own["nickname"])
    if payload["match_id"]:
        key = {"match_id": payload["match_id"], "nickname": identity}
    elif payload["image_fingerprint"]:
        key = {"image_fingerprint": payload["image_fingerprint"], "nickname": identity}
    else:
        roster = [{"nickname": _nickname_key(row["nickname"]), "team": row["team"],
                   **({field: row[field] for field in ("kills", "deaths", "assists")}
                      if index != payload["selected_index"] else {})}
                  for index, row in enumerate(payload["rows"])]
        key = {"nickname": identity, "map": payload["map_name"], "score": payload["score"],
               "played_at": payload["played_at"], "date_label": payload["date_label"], "roster": roster}
    return hashlib.sha256(json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _rated_players(rows, mode, selected, source, personal_rounds=None, score=None):
    players = []
    for index, row in enumerate(rows):
        calculation = calculate(mode, row["kills"], row["deaths"], row["assists"], rounds=personal_rounds if index == selected else None, score=score)
        value = calculation["value"]
        players.append({**row, "selected": index == selected,
                        "user_confirmed": source == "screenshot" and index == selected,
                        "rating": float(value) if value is not None else None,
                        "rating_display": displayed(value), "rating_color": rating_color(value),
                        "rating_tier": calculation['tier'], "rounds_basis": calculation['parameters'].get('rounds_basis')})
    return players


def _snapshot(*, source, snapshot_id, mode, rows, selected, match_id, map_name, score,
              ended_at, date_label, loaded_at, requested_nickname=None, screenshot_id=None,
              original_rating=None, original_rating_tier=None, personal_rounds=None):
    own = rows[selected]
    result = calculate(mode, own["kills"], own["deaths"], own["assists"], rounds=personal_rounds, score=score)
    return {"id": snapshot_id, "screenshot_id": screenshot_id, "source": source,
            "source_label": "Со скриншота" if source == "screenshot" else "Из игровой истории",
            "source_note": SCREENSHOT_NOTE if source == "screenshot" else "Сохранённые данные игрового источника.",
            "mode": mode, "match_id": match_id, "map": map_name, "score": score,
            "ended_at": ended_at, "date_label": date_label, "loaded_at": loaded_at,
            "nickname": own["nickname"], "requested_nickname": requested_nickname,
            # A numeric nickname is not evidence of an actual gaming UID.
            "player_uid": own.get("uid"), "selected_index": selected,
            **{key: own[key] for key in ("kills", "deaths", "assists")},
            "rating": float(result["value"]), "rating_display": displayed(result["value"]),
            "rating_color": rating_color(result["value"]), "formula_version": _version(mode),
            "rating_tier": result["tier"], "personal_rounds": personal_rounds,
            "match_rounds": match_round_count(score), "rounds_basis": result['parameters'].get('rounds_basis'),
            "method_note": "Дуэльная оценка 2×2 · v2" if mode == "allies" else "По подтверждённым личным раундам" if personal_rounds else "По раундам матча · полное участие предполагается" if result['tier'] == 'match_rounds' else METHOD_NOTE,
            "rating_inputs": result["inputs"], "rating_parameters": result["parameters"],
            "rating_factors": result["factors"],
            "players": _rated_players(rows, mode, selected, source, personal_rounds, score),
            "original_rating": float(original_rating) if original_rating is not None else None,
            "original_rating_tier": original_rating_tier}


def _screenshot_snapshot(shot):
    cached = shot.snapshot
    rows = [{key: row.get(key, [] if key == "aliases" else None) for key in ROW_FIELDS}
            for row in cached["players"]]
    return _snapshot(source="screenshot", snapshot_id=f"screenshot:{shot.id}", screenshot_id=shot.id,
                     mode=shot.mode, rows=rows, selected=cached["selected_index"], match_id=shot.match_id,
                     map_name=cached.get("map"), score=cached.get("score"), ended_at=cached.get("ended_at"),
                     date_label=cached.get("date_label"), loaded_at=_iso(shot.created_at),
                     requested_nickname=cached.get("requested_nickname"), personal_rounds=cached.get('personal_rounds'))


def _rounds_sql(model):
    personal = model.snapshot['personal_rounds'].as_integer() if model is m.ScreenshotMatch else model.rounds
    score = model.snapshot['score'] if model is m.ScreenshotMatch else m.Match.score
    left, right = score[0].as_integer(), score[1].as_integer()
    total = left + right
    valid_total = (left >= 0) & (right >= 0) & (total > 0) & (total <= 200) & (model.deaths <= total) & (model.kills <= total * 5)
    return case((personal > 0, personal), (valid_total, total), else_=None)


def _rating_sql(model, mode):
    if mode == "allies":
        p = {key: Decimal(value) for key, value in ALLIES_PARAMETERS.items()}
        numerator = cast(model.kills, Numeric(28, 12)) + p['assist_credit'] * cast(model.assists, Numeric(28, 12)) + p['kill_prior']
        denominator = cast(model.deaths, Numeric(28, 12)) + p['death_prior']
        return func.round(cast(func.power(numerator / denominator, p['exponent']), Numeric(28, 12)), 8)
    norms = PARAMETERS[mode]["match"]
    rounds = _rounds_sql(model)
    value = literal(Decimal("1"), type_=Numeric(28, 12))
    for field in ("kills", "deaths", "assists"):
        divisor = case((rounds > 0, cast(rounds, Numeric(28, 12)) * Decimal(PARAMETERS[mode]['rounds'][field])), else_=Decimal(norms[field]))
        factor = (cast(getattr(model, field), Numeric(28, 12)) / divisor - 1) * Decimal(WEIGHTS[field])
        value = value - factor if field == "deaths" else value + factor
    return func.round(case((value < 0, literal(Decimal(0))), else_=value), 8)


def _automatic_snapshot(db, participant):
    match = db.get(m.Match, participant.match_id)
    people = db.execute(select(m.Participant, m.Player).join(m.Player, m.Player.uid == m.Participant.player_uid)
                        .where(m.Participant.match_id == participant.match_id)
                        .order_by(m.Participant.team, m.Participant.rank, m.Participant.id)).all()
    rows = [{"nickname": player.nickname, "uid": player.uid, "aliases": [], "team": person.team,
             "kills": person.kills, "deaths": person.deaths, "assists": person.assists,
             "game_score": person.game_score, "rank": person.rank}
            for person, player in people]
    selected = next(index for index, row in enumerate(rows) if row["uid"] == participant.player_uid)
    return _snapshot(source="automatic", snapshot_id=f"automatic:{participant.match_id}:{participant.player_uid}",
                     mode=participant.mode, rows=rows, selected=selected, match_id=participant.match_id,
                     map_name=match.map, score=match.score, ended_at=_iso(match.ended_at), date_label=None,
                     loaded_at=_iso(m.utcnow()), original_rating=participant.rating,
                     original_rating_tier=participant.rating_tier, personal_rounds=participant.rounds if participant.mode == 'competitive' else None)


def _best_candidates(db, user, mode):
    candidates = []
    for confirmed in ([True, False] if mode == 'competitive' else [None]):
        query = select(m.ScreenshotMatch).where(m.ScreenshotMatch.telegram_id == user.telegram_id, m.ScreenshotMatch.mode == mode)
        if confirmed is not None:
            query = query.where(_rounds_sql(m.ScreenshotMatch) > 0 if confirmed else func.coalesce(_rounds_sql(m.ScreenshotMatch), 0) <= 0)
        shot = db.scalar(query.order_by(_rating_sql(m.ScreenshotMatch, mode).desc(), m.ScreenshotMatch.created_at, m.ScreenshotMatch.id).limit(1))
        if shot is not None:
            candidates.append(_screenshot_snapshot(shot))
    if user.primary_uid:
        for confirmed in ([True, False] if mode == 'competitive' else [None]):
            query = select(m.Participant).join(m.Match, m.Match.id == m.Participant.match_id).where(
            m.Participant.player_uid == user.primary_uid, m.Participant.mode == mode,
            m.Match.mode == mode, m.Match.completed.is_(True),
            m.Participant.kills.is_not(None), m.Participant.deaths.is_not(None), m.Participant.assists.is_not(None),
            m.Participant.kills >= 0, m.Participant.deaths >= 0, m.Participant.assists >= 0,
            )
            if confirmed is not None:
                query = query.where(m.Participant.rounds > 0 if confirmed else func.coalesce(m.Participant.rounds, 0) <= 0)
            person = db.scalar(query.order_by(_rating_sql(m.Participant, mode).desc(), m.Participant.ended_at, m.Participant.id).limit(1))
            if person is not None:
                candidates.append(_automatic_snapshot(db, person))
    if mode == 'competitive' and any(item['rating_tier'] in {'rounds', 'match_rounds'} for item in candidates):
        return [item for item in candidates if item['rating_tier'] in {'rounds', 'match_rounds'}]
    return candidates


def _best_snapshot_valid(db, best, user, mode):
    if best.formula_version != _version(mode):
        return False
    if best.source == "screenshot":
        shot = db.scalar(select(m.ScreenshotMatch).where(m.ScreenshotMatch.id == best.screenshot_id,
                        m.ScreenshotMatch.telegram_id == user.telegram_id, m.ScreenshotMatch.mode == mode))
        if shot is None:
            return False
        current = _screenshot_snapshot(shot)
        valid = Decimal(str(current["rating"])) == best.rating
        if valid:
            best.snapshot = current
        return valid
    if best.source == "automatic" and best.snapshot.get("player_uid") == user.primary_uid:
        person = db.scalar(select(m.Participant).join(m.Match).where(
            m.Participant.match_id == best.snapshot["match_id"], m.Participant.player_uid == user.primary_uid,
            m.Participant.mode == mode, m.Match.completed.is_(True)))
        if person is None or any(getattr(person, key) is None for key in ("kills", "deaths", "assists")):
            return False
        match = db.get(m.Match, person.match_id)
        result = calculate(mode, person.kills, person.deaths, person.assists, rounds=person.rounds if mode == 'competitive' else None, score=match.score)
        valid = result["value"] == best.rating
        if valid:
            current = _automatic_snapshot(db, person)
            current["loaded_at"] = best.snapshot.get("loaded_at") or current["loaded_at"]
            best.snapshot = current
        return valid
    return False


def _select_best(db, user, mode):
    existing = db.get(m.PersonalBest, (user.telegram_id, mode), populate_existing=True)
    if existing is not None and not _best_snapshot_valid(db, existing, user, mode):
        # A corrected winning screenshot, changed primary UID or formula version
        # invalidates the old value. Rebuild from stored raw K/D/A, never keep it.
        db.delete(existing)
        db.flush()
        existing = None
    candidates = _best_candidates(db, user, mode)
    if not candidates:
        return None
    candidate = max(candidates, key=lambda item: Decimal(str(item["rating"])))
    if existing is not None and ((existing.snapshot.get('rating_tier') in {'rounds', 'match_rounds'}) != (candidate['rating_tier'] in {'rounds', 'match_rounds'})):
        db.delete(existing)
        db.flush()
        existing = None
    if existing is not None and candidate["id"] == existing.source_key:
        candidate["loaded_at"] = existing.snapshot.get("loaded_at") or candidate["loaded_at"]
    value = Decimal(str(candidate["rating"]))
    statement = _insert(db, m.PersonalBest).values(
        telegram_id=user.telegram_id, mode=mode, source=candidate["source"], source_key=candidate["id"],
        screenshot_id=candidate["screenshot_id"], rating=value, formula_version=_version(mode),
        snapshot=candidate, updated_at=m.utcnow())
    excluded = statement.excluded
    db.execute(statement.on_conflict_do_update(
        index_elements=[m.PersonalBest.telegram_id, m.PersonalBest.mode],
        set_={key: getattr(excluded, key) for key in ("source", "source_key", "screenshot_id", "rating", "formula_version", "snapshot", "updated_at")},
        where=excluded.rating > m.PersonalBest.rating,
    ))
    stored = db.get(m.PersonalBest, (user.telegram_id, mode), populate_existing=True)
    if stored.source_key == candidate["id"] and stored.rating == value:
        # Refresh metadata of the SAME winning match without replacing a tie.
        stored.snapshot = candidate
        db.flush()
    return deepcopy(stored.snapshot)


def save_screenshot_match(db, telegram_id: int, payload: dict) -> dict:
    """Persist a user-confirmed board; caller commits/rolls back its transaction."""
    payload = _normalize_payload(payload)
    user = ensure_personal_user(db, telegram_id, lock=True)
    mode = payload["mode"]
    previous = db.get(m.PersonalBest, (telegram_id, mode))
    previous_exists = previous is not None
    previous_rating = previous.rating if previous is not None and previous.formula_version == _version(mode) else None
    dedup_key = _dedup_key(payload)
    shot = db.scalar(select(m.ScreenshotMatch).where(m.ScreenshotMatch.telegram_id == telegram_id,
                     m.ScreenshotMatch.mode == mode, m.ScreenshotMatch.dedup_key == dedup_key))
    now = m.utcnow()
    own = payload["rows"][payload["selected_index"]]
    user.screenshot_nickname = payload['requested_nickname'] or own['nickname']
    calculation = calculate(mode, own["kills"], own["deaths"], own["assists"], rounds=payload.get('personal_rounds'), score=payload.get('score'))
    if shot is None:
        shot = m.ScreenshotMatch(telegram_id=telegram_id, mode=mode, dedup_key=dedup_key,
                                created_at=now, snapshot={})
        db.add(shot)
    shot.match_id, shot.nickname = payload["match_id"], own["nickname"]
    shot.kills, shot.deaths, shot.assists = own["kills"], own["deaths"], own["assists"]
    shot.rating, shot.formula_version, shot.updated_at = calculation["value"], _version(mode), now
    db.flush()
    shot.snapshot = _snapshot(source="screenshot", snapshot_id=f"screenshot:{shot.id}", screenshot_id=shot.id,
        mode=mode, rows=payload["rows"], selected=payload["selected_index"], match_id=payload["match_id"],
        map_name=payload["map_name"], score=payload["score"], ended_at=payload["played_at"], personal_rounds=payload.get('personal_rounds'),
        date_label=payload["date_label"], loaded_at=_iso(shot.created_at), requested_nickname=payload["requested_nickname"])
    db.flush()
    best = _select_best(db, user, mode)
    is_new = (best is not None and best["id"] == f"screenshot:{shot.id}"
              and (not previous_exists or (previous_rating is not None and Decimal(str(best["rating"])) > previous_rating)))
    return {"match": deepcopy(shot.snapshot), "best": best, "is_new_best": is_new}


def get_screenshot_nickname(db, telegram_id):
    user = db.get(m.User, telegram_id)
    if user is not None and user.screenshot_nickname:
        return user.screenshot_nickname
    # Reuse an already confirmed old upload after upgrading; no fake identity.
    shot = db.scalar(select(m.ScreenshotMatch).where(m.ScreenshotMatch.telegram_id == telegram_id)
                     .order_by(m.ScreenshotMatch.updated_at.desc(), m.ScreenshotMatch.id.desc()).limit(1))
    return (shot.snapshot.get('requested_nickname') or shot.nickname) if shot else None


def get_personal_best(db, user, mode):
    mode = _mode(mode)
    user = ensure_personal_user(db, user.telegram_id, lock=True)
    return _select_best(db, user, mode)


def list_screenshot_matches(db, user, mode, limit=50):
    mode = _mode(mode)
    limit = max(1, min(int(limit), 100))
    query = select(m.ScreenshotMatch).where(m.ScreenshotMatch.telegram_id == user.telegram_id,
                                            m.ScreenshotMatch.mode == mode)
    rows = db.scalars(query.order_by(m.ScreenshotMatch.created_at.desc(), m.ScreenshotMatch.id.desc()).limit(limit))
    total = db.scalar(select(func.count()).select_from(m.ScreenshotMatch).where(
        m.ScreenshotMatch.telegram_id == user.telegram_id, m.ScreenshotMatch.mode == mode))
    return {"matches": [_screenshot_snapshot(row) for row in rows], "total": total, "limit": limit, "truncated": total > limit}


def get_screenshot_match(db, user, screenshot_id):
    shot = db.scalar(select(m.ScreenshotMatch).where(m.ScreenshotMatch.id == screenshot_id,
                                                   m.ScreenshotMatch.telegram_id == user.telegram_id))
    return _screenshot_snapshot(shot) if shot is not None else None
