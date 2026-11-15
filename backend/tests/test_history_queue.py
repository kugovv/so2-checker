import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app import models as m
from app.service import ingest_page, observation, queue_refresh, recent_rows, serialize_profile, upsert_profile
from app.source import HistoryPage, SourceUnavailable
from tests.helpers import FakeSource, match, profile


def test_dedup_and_archived_headline_last20(db):
    player = upsert_profile(db, profile())
    matches = [match(f"m{i}", day=i) for i in range(1, 26)]
    ingest_page(db, player.uid, "competitive", HistoryPage(matches))
    ingest_page(db, player.uid, "competitive", HistoryPage(matches))
    db.commit()
    assert db.scalar(select(func.count()).select_from(m.Match)) == 25
    assert db.scalar(select(func.count()).select_from(m.Participant)) == 25
    archived = serialize_profile(db, player, "competitive", "archive")
    assert archived["summary"]["count"] == 20 and len(archived["matches"]) == 25
    assert archived["history_complete"] is False


def test_gap_detection_disjoint_window_not_just_dates(db):
    upsert_profile(db, profile())
    ingest_page(db, "12345678", "competitive", HistoryPage([match("m1", day=1), match("m2", day=2)]))
    ingest_page(db, "12345678", "competitive", HistoryPage([match("m2", day=2), match("m3", day=3)]))
    assert db.scalar(select(func.count()).select_from(m.HistoryGap)) == 0
    ingest_page(db, "12345678", "competitive", HistoryPage([match("m8", day=8)]))
    assert db.scalar(select(func.count()).select_from(m.HistoryGap)) == 1
    gap = db.scalar(select(m.HistoryGap))
    assert gap.after_at is not None and gap.before_at is not None


def test_cancelled_wrong_mode_and_missing_player_ignored(db):
    upsert_profile(db, profile())
    cancelled = match("cancelled")
    cancelled.completed = False
    other = match("other", uid="999")
    ingest_page(db, "12345678", "competitive", HistoryPage([cancelled, other, match("allies", mode="allies")]))
    assert len(recent_rows(db, "12345678", "competitive")) == 0


def test_queue_duplicate_caching_and_expiry(db):
    upsert_profile(db, profile())
    first = queue_refresh(db, "12345678", "competitive")
    again = queue_refresh(db, "12345678", "competitive")
    assert first.id == again.id
    assert db.scalar(select(func.count()).select_from(m.RefreshJob)) == 1
    first.state = "done"
    obs = observation(db, "12345678", "competitive")
    obs.updated_at = m.utcnow()
    assert queue_refresh(db, "12345678", "competitive").state == "done"
    obs.updated_at = m.utcnow() - timedelta(minutes=10)
    assert queue_refresh(db, "12345678", "competitive").state == "pending"
    assert queue_refresh(db, "12345678", "allies").id != first.id


def test_worker_failure_keeps_cached_data_and_bounded_retries(db_factory, monkeypatch):
    import app.worker as worker

    class BrokenSource(FakeSource):
        async def fetch_history(self, profile, mode, cursor=None):
            raise SourceUnavailable("sensitive-string-must-not-be-stored")

    monkeypatch.setattr(worker, "source", BrokenSource())
    monkeypatch.setattr(worker, "SessionLocal", db_factory)
    with db_factory() as db:
        upsert_profile(db, profile())
        ingest_page(db, "12345678", "competitive", HistoryPage([match()]))
        job = queue_refresh(db, "12345678", "competitive", force=True)
        job.state = "running"
        job.attempts = 3
        job_id = job.id
        db.commit()
    asyncio.run(worker.process_job(job_id))
    with db_factory() as db:
        assert len(recent_rows(db, "12345678", "competitive")) == 1
        job = db.get(m.RefreshJob, job_id)
        assert job.state == "failed"
        assert "sensitive" not in job.error
        assert observation(db, "12345678", "competitive").source_error is not None


def test_worker_pagination_bridges_gap_atomically(db_factory, monkeypatch):
    import app.worker as worker
    monkeypatch.setattr(worker, "SessionLocal", db_factory)
    monkeypatch.setattr(worker.get_settings(), "source_min_interval", 0)

    class PagedSource(FakeSource):
        supports_pagination = True
        async def fetch_history(self, profile, mode, cursor=None):
            if cursor is None:
                return HistoryPage([match("new", day=8)], next_cursor="page2")
            return HistoryPage([match("old", day=1)])

    monkeypatch.setattr(worker, "source", PagedSource())
    with db_factory() as db:
        upsert_profile(db, profile())
        ingest_page(db, "12345678", "competitive", HistoryPage([match("old", day=1)]))
        job = queue_refresh(db, "12345678", "competitive", force=True)
        job.attempts = 1
        job_id = job.id
        db.commit()
    asyncio.run(worker.process_job(job_id))
    with db_factory() as db:
        assert db.get(m.RefreshJob, job_id).state == "done"
        assert db.scalar(select(func.count()).select_from(m.HistoryGap)) == 0
        assert len(recent_rows(db, "12345678", "competitive")) == 2


def test_top_full_sample_only_and_separate_modes(db):
    from app.top import get_leaderboard
    complete = upsert_profile(db, profile("111"))
    partial = upsert_profile(db, profile("222"))
    fallback = upsert_profile(db, profile("333"))
    untracked = upsert_profile(db, profile("444"))
    for i, person in enumerate((complete, partial, fallback), 1):
        db.add(m.User(telegram_id=i, primary_uid=person.uid))
    for i in range(1, 21):
        ingest_page(db, "111", "competitive", HistoryPage([match(f"a{i}", uid="111", day=i)]))
        ingest_page(db, "333", "competitive", HistoryPage([match(f"b{i}", uid="333", day=i, rounds=None)]))
        ingest_page(db, "444", "competitive", HistoryPage([match(f"c{i}", uid="444", day=i)]))
    ingest_page(db, "222", "competitive", HistoryPage([match("partial", uid="222")]))
    db.commit()
    top = get_leaderboard(db, "competitive")
    assert [item["uid"] for item in top["players"]] == ["111"]
    assert {item["uid"] for item in top["provisional"]} == {"222", "333"}
    assert next(item for item in top["provisional"] if item["uid"] == "333")["tier"] == "match"
    assert get_leaderboard(db, "allies")["players"] == []
