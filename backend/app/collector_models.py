"""Durable protocol-1 transport. Evidence is distinct from verified game data."""
from datetime import datetime
from typing import Optional
from sqlalchemy import String,Integer,Boolean,DateTime,ForeignKey,JSON,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .models import utcnow


class TrackedProfile(Base):
    __tablename__='collector_profiles'
    uid: Mapped[str]=mapped_column(String(32),primary_key=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class ProfileState(Base):
    __tablename__='collector_profile_states'
    uid: Mapped[str]=mapped_column(String(32),ForeignKey('collector_profiles.uid'),primary_key=True)
    mode: Mapped[str]=mapped_column(String(20),primary_key=True)
    status: Mapped[str]=mapped_column(String(32),default='awaiting_first')
    last_success_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    error_code: Mapped[Optional[str]]=mapped_column(String(64))


class Watch(Base):
    __tablename__='collector_watches'
    telegram_id: Mapped[int]=mapped_column(ForeignKey('users.telegram_id'),primary_key=True)
    uid: Mapped[str]=mapped_column(String(32),ForeignKey('collector_profiles.uid'),primary_key=True)
    kind: Mapped[str]=mapped_column(String(20),primary_key=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class Device(Base):
    __tablename__='collector_devices'
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    label: Mapped[str]=mapped_column(String(80))
    token_hash: Mapped[str]=mapped_column(String(64),unique=True)
    bound_device_id: Mapped[Optional[str]]=mapped_column(String(128),unique=True)
    revoked: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    last_seen_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__='collector_jobs'
    __table_args__=(UniqueConstraint('uid','mode','batch_key',name='uq_collector_profile_mode_batch'),Index('ix_collector_job_claim','status','created_at'))
    id: Mapped[str]=mapped_column(String(80),primary_key=True)
    uid: Mapped[str]=mapped_column(String(32),ForeignKey('collector_profiles.uid'))
    mode: Mapped[str]=mapped_column(String(20))
    batch_key: Mapped[str]=mapped_column(String(80))
    origin: Mapped[str]=mapped_column(String(20),default='night')
    status: Mapped[str]=mapped_column(String(32),default='queued')
    max_matches: Mapped[int]=mapped_column(Integer,default=20)
    active_lease_id: Mapped[Optional[str]]=mapped_column(String(32))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    completed_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))


class Lease(Base):
    __tablename__='collector_leases'
    __table_args__=(UniqueConstraint('job_id','device_id','run_id',name='uq_collector_lease_run'),Index('ix_collector_lease_expiry','status','expires_at'))
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    job_id: Mapped[str]=mapped_column(String(80),ForeignKey('collector_jobs.id'))
    device_id: Mapped[str]=mapped_column(String(32),ForeignKey('collector_devices.id'))
    run_id: Mapped[str]=mapped_column(String(128))
    status: Mapped[str]=mapped_column(String(32),default='active')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    steps_issued: Mapped[int]=mapped_column(Integer,default=0)
    frame_count: Mapped[int]=mapped_column(Integer,default=0)
    match_frame_count: Mapped[int]=mapped_column(Integer,default=0)
    plan: Mapped[dict]=mapped_column(JSON,default=dict)
    completion: Mapped[Optional[dict]]=mapped_column(JSON)


class Frame(Base):
    __tablename__='collector_frames'
    __table_args__=(UniqueConstraint('lease_id','sequence',name='uq_collector_frame_sequence'),Index('ix_collector_frame_parse','status','created_at'))
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    lease_id: Mapped[str]=mapped_column(String(32),ForeignKey('collector_leases.id'))
    sequence: Mapped[int]=mapped_column(Integer)
    sha256: Mapped[str]=mapped_column(String(64))
    storage_path: Mapped[str]=mapped_column(String(256))
    byte_size: Mapped[int]=mapped_column(Integer)
    width: Mapped[int]=mapped_column(Integer)
    height: Mapped[int]=mapped_column(Integer)
    label: Mapped[str]=mapped_column(String(128))
    captured_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    status: Mapped[str]=mapped_column(String(32),default='queued')
    parser_version: Mapped[Optional[str]]=mapped_column(String(64))
    match_id: Mapped[Optional[str]]=mapped_column(String(128),index=True)
    analysis: Mapped[dict]=mapped_column(JSON,default=dict)
    response: Mapped[dict]=mapped_column(JSON,default=dict)


class Candidate(Base):
    __tablename__='collector_candidates'
    __table_args__=(UniqueConstraint('uid','mode','match_id',name='uq_collector_candidate_match'),)
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    uid: Mapped[str]=mapped_column(String(32),ForeignKey('collector_profiles.uid'))
    mode: Mapped[str]=mapped_column(String(20))
    match_id: Mapped[str]=mapped_column(String(128),index=True)
    frame_id: Mapped[str]=mapped_column(String(32),ForeignKey('collector_frames.id'))
    provenance: Mapped[str]=mapped_column(String(20),default='collector')
    verification_status: Mapped[str]=mapped_column(String(32),default='needs_review')
    snapshot: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class Event(Base):
    __tablename__='collector_events'
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    device_id: Mapped[str]=mapped_column(String(32),ForeignKey('collector_devices.id'))
    run_id: Mapped[str]=mapped_column(String(128))
    code: Mapped[str]=mapped_column(String(64))
    occurred_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
