"""Durable queue, one fetch at a time, no continuous scan of followed profiles."""
import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import select, text

from . import models as m
from .config import get_settings
from .db import SessionLocal, engine
from .service import aware, ingest_page, observation, upsert_profile
from .source import HistoryPage, SourceProfile, SourceUnavailable, source

logger = logging.getLogger("so2.worker")


async def process_job(job_id):
    config = get_settings()
    with SessionLocal() as db:
        job = db.get(m.RefreshJob, job_id)
        player = db.get(m.Player, job.player_uid)
        try:
            profile = await asyncio.wait_for(source.resolve_uid(player.uid), config.source_timeout)
            if profile.uid != player.uid:
                raise SourceUnavailable("Несоответствие игрового ID")
            upsert_profile(db, profile)
            cursor, seen_cursors, all_matches = None, set(), []
            # Pagination is enabled only after the actual provider capability is verified.
            for page_index in range(config.history_max_pages):
                page = await asyncio.wait_for(source.fetch_history(profile, job.mode, cursor), config.source_timeout)
                all_matches.extend(page.matches)
                if not source.supports_pagination or not page.next_cursor or page.next_cursor in seen_cursors:
                    break
                seen_cursors.add(page.next_cursor)
                cursor = page.next_cursor
                await asyncio.sleep(config.source_min_interval)
            # Detect continuity after every fetched page is considered; a deeper page may bridge the latest window.
            ingest_page(db, player.uid, job.mode, HistoryPage(all_matches, page.next_cursor, page.history_complete))
            job.state = "done"
            job.finished_at = m.utcnow()
            job.error = None
            db.commit()
        except Exception as exc:
            # Never store raw exceptions: upstream URLs and session tokens may appear there.
            db.rollback()
            job = db.get(m.RefreshJob, job_id)
            message = "Источник статистики временно недоступен. Сохранённые данные доступны."
            obs = observation(db, job.player_uid, job.mode)
            obs.source_error = message
            job.error = message
            job.state = "pending" if job.attempts < 3 else "failed"
            job.requested_at = m.utcnow() + timedelta(seconds=30 * 2 ** job.attempts)
            job.finished_at = m.utcnow()
            db.commit()
            logger.warning("Refresh failed (%s), job=%s", type(exc).__name__, job_id)


async def run():
    config = get_settings()
    lock_connection = engine.connect()
    if engine.dialect.name == "postgresql":
        if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(72910423)")):
            raise RuntimeError("An update worker is already running")
    try:
        if config.collector_enabled:
            from .collector_worker import recover_and_schedule
            recover_and_schedule()
        # On a process restart, interrupted requests are safe to retry because ingestion is atomic.
        with SessionLocal() as db:
            for job in db.scalars(select(m.RefreshJob).where(m.RefreshJob.state == "running")):
                job.state = "pending"
                job.requested_at = m.utcnow()
            db.commit()
        collector_tick=0;collector_prune=0
        while True:
            if config.collector_enabled:
                from .collector_worker import process_frame,schedule_tick,prune_evidence
                if time.monotonic()-collector_tick>30:
                    schedule_tick();collector_tick=time.monotonic()
                if time.monotonic()-collector_prune>3600:
                    prune_evidence();collector_prune=time.monotonic()
                if await process_frame():continue
            job_id = None
            with SessionLocal() as db:
                job = db.scalar(select(m.RefreshJob).where(m.RefreshJob.state == "pending", m.RefreshJob.requested_at <= m.utcnow())
                                .order_by(m.RefreshJob.requested_at).with_for_update(skip_locked=True).limit(1))
                if job:
                    job_id = job.id
                    job.state = "running"
                    job.started_at = m.utcnow()
                    job.attempts += 1
                    db.commit()
            if job_id is not None:
                await process_job(job_id)
                await asyncio.sleep(config.source_min_interval)
            else:
                await asyncio.sleep(1)
    finally:
        lock_connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run())
