import threading
import time
from collections import defaultdict
from weakref import WeakKeyDictionary

from sqlalchemy import func, select

from . import models as m
from .rating import VERSION, summarize

_cache = WeakKeyDictionary()
_cache_lock = threading.Lock()


def get_leaderboard(db, mode):
    """Three bounded SQL queries and a 30-second cache, instead of 10k N+1 profile reads."""
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
    groups = defaultdict(list)
    for row in db.scalars(select(m.Participant).join(ranked, m.Participant.id == ranked.c.id).where(ranked.c.position <= 20)):
        groups[row.player_uid].append(row)
    players, provisional = [], []
    if groups:
        for player in db.scalars(select(m.Player).where(m.Player.uid.in_(groups))):
            summary = summarize(groups[player.uid])
            if summary["rating"] is None:
                continue
            item = {"uid": player.uid, "nickname": player.nickname, **summary}
            if summary["count"] == 20 and summary["rated_count"] == 20 and summary["tier"] == "rounds":
                players.append(item)
            else:
                provisional.append(item)
    players.sort(key=lambda p: (-p["rating"], p["uid"]))
    provisional.sort(key=lambda p: (p["tier"] or "", -p["rating"], p["uid"]))
    result = {"players": players[:100], "provisional": provisional[:100],
              "scope": "Среди отслеживаемых игроков", "formula_version": VERSION, "cache_seconds": 30}
    with _cache_lock:
        _cache.setdefault(engine, {})[mode] = (time.monotonic(), result)
    return result
