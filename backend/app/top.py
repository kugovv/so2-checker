import threading
import time
from bisect import insort
from itertools import groupby
from weakref import WeakKeyDictionary

from sqlalchemy import func, select

from . import models as m
from .rating import VERSION, summarize

_cache = WeakKeyDictionary()
_cache_lock = threading.Lock()


def get_leaderboard(db, mode):
    """Stream compact rows, retain <=20 per player and <=100 per table; cache for 30s."""
    engine = db.get_bind()
    with _cache_lock:
        cached = _cache.get(engine, {}).get(mode)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
    followed = select(m.User.primary_uid).where(m.User.primary_uid.is_not(None)).distinct()
    ranked = select(m.Participant.id, func.row_number().over(
        partition_by=m.Participant.player_uid,
        order_by=(m.Participant.ended_at.desc(), m.Participant.match_id.desc())
    ).label("position")).join(m.Match, m.Participant.match_id == m.Match.id).where(
        m.Participant.player_uid.in_(followed), m.Participant.mode == mode, m.Match.completed.is_(True)
    ).subquery()
    # Select only scalar inputs used by summarize: raw JSON, factors and ORM objects stay in the DB.
    query = select(
        m.Participant.player_uid, m.Participant.rating, m.Participant.kills, m.Participant.deaths,
        m.Participant.assists, m.Participant.headshots, m.Participant.rating_tier, m.Participant.formula_version,
    ).join(ranked, m.Participant.id == ranked.c.id).where(ranked.c.position <= 20).order_by(
        m.Participant.player_uid
    ).execution_options(yield_per=1000)
    players, provisional = [], []
    with db.execute(query) as rows:
        for uid, group in groupby(rows, key=lambda row: row.player_uid):
            summary = summarize(list(group))
            if summary["rating"] is None:
                continue
            item = {"uid": uid, **summary}
            if summary["count"] == 20 and summary["rated_count"] == 20 and summary["tier"] == "rounds":
                insort(players, item, key=lambda p: (-p["rating"], p["uid"]))
                del players[100:]
            else:
                insort(provisional, item, key=lambda p: (p["tier"] or "", -p["rating"], p["uid"]))
                del provisional[100:]
    finalists = players + provisional
    if finalists:
        names = dict(db.execute(select(m.Player.uid, m.Player.nickname).where(
            m.Player.uid.in_([item["uid"] for item in finalists])
        )).all())
        for item in finalists:
            item["nickname"] = names[item["uid"]]
    result = {"players": players, "provisional": provisional,
              "scope": "Среди отслеживаемых игроков", "formula_version": VERSION, "cache_seconds": 30}
    with _cache_lock:
        _cache.setdefault(engine, {})[mode] = (time.monotonic(), result)
    return result
