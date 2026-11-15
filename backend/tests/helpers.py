import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.config import get_settings
from app.source import HistoryPage, SourceMatch, SourceParticipant, SourceProfile


def signed_data(user_id=100, age=0, token=None, now=None, **extra):
    token = token or get_settings().bot_token
    current = int(time.time()) if now is None else now
    data = {"auth_date": str(current - age), "query_id": "test-query", "user": json.dumps({"id": user_id}), **extra}
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def login(client, user_id=100):
    response = client.post("/api/auth", json={"init_data": signed_data(user_id)})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["session_token"]
    return response


def profile(uid="12345678"):
    return SourceProfile(uid=uid, source_id="internal-" + uid, nickname="Игрок " + uid)


def match(match_id="m1", uid="12345678", mode="competitive", day=1, kills=15, deaths=15, assists=4, headshots=5, rounds=20):
    return SourceMatch(id=match_id, mode=mode, map="Sandstone", ended_at=datetime(2026, 10, day, tzinfo=timezone.utc),
                       score=[10, 8], participants=[SourceParticipant(profile(uid), team="A", result="win",
                       kills=kills, deaths=deaths, assists=assists, headshots=headshots, rounds=rounds)])


class FakeSource:
    """Only used in tests. Never imported by the production application."""
    available = True
    supports_pagination = False
    message = "Test source"

    def __init__(self):
        self.calls = 0
        self.matches = [match()]

    async def resolve_uid(self, uid):
        self.calls += 1
        return profile(uid)

    async def fetch_history(self, profile, mode, cursor=None):
        self.calls += 1
        return HistoryPage([m for m in self.matches if m.mode == mode])
