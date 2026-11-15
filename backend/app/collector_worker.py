"""One bounded frame at a time; uncalibrated evidence never becomes game truth."""
import asyncio
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
from sqlalchemy import select
from . import models as m,collector_models as c,collector
from .db import SessionLocal
from .config import get_settings
from .screenshot_ocr import _run_worker,analyze_image


async def process_frame():
    with SessionLocal() as db:
        frame=db.scalar(select(c.Frame).where(c.Frame.status=='queued').order_by(c.Frame.created_at).with_for_update(skip_locked=True).limit(1))
        if frame is None:return False
        frame_id=frame.id;frame.status='processing';db.commit()
        path=(Path(get_settings().collector_storage)/frame.storage_path).resolve()
        if not path.is_relative_to(Path(get_settings().collector_storage).resolve()):return False
        try:raw=path.read_bytes()
        except OSError:
            frame.status='needs_review';frame.analysis={'reason':'EVIDENCE_UNAVAILABLE'};db.commit();return True
    analysis={};board=None;status='needs_review'
    try:
        result,payload=await asyncio.wait_for(_run_worker(raw,module='app.collector_vision_worker'),timeout=22)
        if result!=0:raise ValueError('invalid screen')
        analysis=json.loads(payload)
        match_id=analysis.get('match_id')
        if match_id:
            with SessionLocal() as db:
                verified=db.scalar(select(c.Candidate).where(c.Candidate.match_id==match_id,c.Candidate.verification_status=='verified').limit(1))
            if verified:status='duplicate'
            else:board=await analyze_image(raw)
        # Unknown screens cause no taps. Calibrated plans must be proved on the
        # real phone before any navigation or profile-success transition exists.
        analysis['reason']='CALIBRATION_REQUIRED'
    except Exception:
        analysis={'reason':'SCREEN_OR_OCR_UNCERTAIN'}
    with SessionLocal() as db:
        frame=db.get(c.Frame,frame_id);frame.status=status;frame.parser_version=collector.PARSER_VERSION
        frame.match_id=analysis.get('match_id');frame.analysis=analysis
        lease=db.get(c.Lease,frame.lease_id);job=db.get(c.Job,lease.job_id)
        if board is not None and board.match_id:
            if board.mode!=job.mode:frame.analysis={**analysis,'reason':'MODE_MISMATCH'}
            else:
                query=select(c.Candidate).where(c.Candidate.uid==job.uid,c.Candidate.mode==job.mode,c.Candidate.match_id==board.match_id)
                candidate=db.scalar(query)
                if candidate is None:
                    db.add(c.Candidate(id=collector.new_id(),uid=job.uid,mode=job.mode,match_id=board.match_id,
                                       frame_id=frame.id,snapshot=asdict(board)))
                # The table alone does not prove the requested profile UID.
                frame.analysis={**analysis,'reason':'PROFILE_IDENTITY_AND_NAVIGATION_NOT_VERIFIED'}
        if job.status=='captured':job.status='partial'
        db.commit()
    return True


def recover_and_schedule():
    with SessionLocal() as db:
        collector.expire_leases(db);collector.materialize_night(db)
        # One worker owns the existing PG advisory lock; interrupted parses retry.
        for frame in db.scalars(select(c.Frame).where(c.Frame.status=='processing')):frame.status='queued'
        db.commit()


def schedule_tick():
    with SessionLocal() as db:
        collector.expire_leases(db);collector.materialize_night(db);db.commit()


def prune_evidence():
    root=Path(get_settings().collector_storage).resolve()
    with SessionLocal() as db:
        rows=db.scalars(select(c.Frame).where(c.Frame.created_at<m.utcnow()-timedelta(days=7),c.Frame.status.notin_(['queued','processing']),c.Frame.byte_size>0))
        for frame in rows:
            path=(root/frame.storage_path).resolve()
            if path.is_relative_to(root) and path.is_file():path.unlink()
            frame.storage_path='';frame.byte_size=0
        db.commit()
