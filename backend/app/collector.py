"""Protocol-1 state and schedule. Transport completion never verifies a profile."""
from datetime import datetime,timedelta,time,timezone
import hashlib
import math
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from . import models as m,collector_models as c
from .config import get_settings
from .service import aware

MSK=ZoneInfo('Europe/Moscow')
CAPABILITIES={'accessibility_screenshot','tap','swipe','set_text','max_matches_20'}
PARSER_VERSION='night-bridge-ocr-1'


def new_id(): return uuid.uuid4().hex


def token_hash(token): return hashlib.sha256(token.encode('ascii')).hexdigest()


def fail(status,message): raise HTTPException(status,message)


def insert(db,model):
    return (pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert)(model)


def validate_uid(uid):
    # Storage/abuse bound, not a claim that game UIDs have a fixed length.
    if not isinstance(uid,str) or not re.fullmatch(r'[0-9]{1,20}',uid,re.ASCII):
        fail(422,'Игровой ID должен содержать только цифры (до 20 знаков).')
    return uid


def register_profile(db,uid):
    validate_uid(uid)
    db.execute(insert(db,c.TrackedProfile).values(uid=uid,created_at=m.utcnow()).on_conflict_do_nothing(index_elements=['uid']))
    for mode in ('competitive','allies'):
        db.execute(insert(db,c.ProfileState).values(uid=uid,mode=mode,status='awaiting_first').on_conflict_do_nothing(index_elements=['uid','mode']))
    db.flush()
    return db.get(c.TrackedProfile,uid)


def watch_profile(db,user,uid,kind='primary'):
    register_profile(db,uid)
    if kind!='primary':
        count=db.scalar(select(func.count()).select_from(c.Watch).where(c.Watch.telegram_id==user.telegram_id,c.Watch.kind!='primary',c.Watch.created_at>=m.utcnow()-timedelta(days=1)))
        if count>=5 and db.get(c.Watch,(user.telegram_id,uid,kind)) is None:
            fail(429,'На сегодня достигнут лимит новых профилей для планового просмотра.')
    db.execute(insert(db,c.Watch).values(telegram_id=user.telegram_id,uid=uid,kind=kind,created_at=m.utcnow()).on_conflict_do_nothing(index_elements=['telegram_id','uid','kind']))


def next_night(now=None):
    local=(now or m.utcnow()).astimezone(MSK)
    return datetime.combine(local.date()+timedelta(days=1),time.min,MSK)


def profile_status(db,uid,mode,now=None):
    now=now or m.utcnow();state=db.get(c.ProfileState,(uid,mode))
    status=state.status if state else 'awaiting_first'
    last=aware(state.last_success_at) if state and state.last_success_at else None
    if last is None:
        previous=db.scalar(select(m.Observation).where(m.Observation.player_uid==uid,m.Observation.mode==mode))
        if previous and previous.updated_at:
            last=aware(previous.updated_at)
            if status=='awaiting_first':status='updated'
    labels={'awaiting_first':'Ожидает первого обновления','queued':'Ожидает очереди','processing':'Обрабатывается','updated':'Обновлён','partial':'Обновление не завершено','failed':'Обновление не завершено'}
    return {'status':status,'label':labels.get(status,'Обновление не завершено'),
            'last_success_at':last.isoformat() if last else None,
            'next_run_at':next_night(now).isoformat(),'timezone':'Europe/Moscow',
            'schedule_enabled':get_settings().collector_schedule_enabled,
            'navigation_calibrated':False,
            'last_success_msk':last.astimezone(MSK).strftime('%d.%m.%Y %H:%M МСК') if last else None,
            'next_run_msk':next_night(now).strftime('%d.%m.%Y, 00:00 МСК')}


def enqueue(db,uid,mode,batch_key,origin='night'):
    if mode not in {'competitive','allies'}: fail(400,'Unsupported mode')
    register_profile(db,uid)
    # Lock one shared profile, deduplicating subscriptions from multiple owners.
    db.scalar(select(c.TrackedProfile).where(c.TrackedProfile.uid==uid).with_for_update())
    existing=db.scalar(select(c.Job).where(c.Job.uid==uid,c.Job.mode==mode,c.Job.status.in_(['queued','leased'])).order_by(c.Job.created_at).limit(1))
    if existing: return existing
    existing=db.scalar(select(c.Job).where(c.Job.uid==uid,c.Job.mode==mode,c.Job.batch_key==batch_key))
    if existing:return existing
    job=c.Job(id=new_id(),uid=uid,mode=mode,batch_key=batch_key,origin=origin,status='queued',max_matches=20)
    db.add(job);db.get(c.ProfileState,(uid,mode)).status='queued';db.flush()
    return job


def materialize_night(db,now=None):
    now=now or m.utcnow();local=now.astimezone(MSK)
    if not get_settings().collector_schedule_enabled or local.hour>=3:return 0
    users=db.scalars(select(m.User).where((m.User.preferred_uid.is_not(None)) | (m.User.primary_uid.is_not(None))))
    uids=set()
    for user in users:
        uid=user.preferred_uid or user.primary_uid
        watch_profile(db,user,uid);uids.add(uid)
    uids.update(db.scalars(select(c.Watch.uid).where(c.Watch.kind!='primary',c.Watch.created_at>=now-timedelta(days=7))))
    midnight=datetime.combine(local.date(),time.min,MSK)
    count=0
    for uid in sorted(uids):
        for mode in ('competitive','allies'):
            state=db.get(c.ProfileState,(uid,mode))
            if state and state.last_success_at and aware(state.last_success_at)>=midnight:continue
            enqueue(db,uid,mode,'night:'+local.date().isoformat());count+=1
    return count


def expire_leases(db,now=None):
    now=now or m.utcnow()
    leases=list(db.scalars(select(c.Lease).where(c.Lease.status=='active',c.Lease.expires_at<=now)))
    for previous in leases:
        job=db.scalar(select(c.Job).where(c.Job.id==previous.job_id).with_for_update(skip_locked=True))
        if job is None:continue
        lease=db.scalar(select(c.Lease).where(c.Lease.id==previous.id).with_for_update().execution_options(populate_existing=True))
        if lease.status!='active' or aware(lease.expires_at)>now:continue
        lease.status='expired'
        if job.active_lease_id==lease.id:
            job.active_lease_id=None;job.status='queued'
            db.get(c.ProfileState,(job.uid,job.mode)).status='partial'


def validate_steps(steps):
    allowed={'launch_game':set(),'back':set(),'done':set(),'wait':{'ms'},'tap':{'x','y'},'swipe':{'x1','y1','x2','y2','duration_ms'},'set_text':{'text','view_id'},'capture':{'label'}}
    if not isinstance(steps,list) or not 1<=len(steps)<=120:fail(400,'Steps must contain 1..120 actions')
    captures=0;matches=0
    for step in steps:
        if not isinstance(step,dict) or step.get('action') not in allowed:fail(400,'Unknown action')
        action=step['action']
        if set(step)-({'action'}|allowed[action]):fail(400,'Unexpected action fields')
        if action=='wait' and (type(step.get('ms')) is not int or not 0<=step['ms']<=10000):fail(400,'Invalid wait')
        for field in ({'tap':['x','y'],'swipe':['x1','y1','x2','y2']}.get(action,[])):
            value=step.get(field)
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:fail(400,'Invalid coordinate')
        if action=='swipe' and (type(step.get('duration_ms')) is not int or not 100<=step['duration_ms']<=1500):fail(400,'Invalid swipe')
        if action=='set_text':
            if not isinstance(step.get('text'),str) or not re.fullmatch('[0-9]{1,20}',step['text']):fail(400,'Only a game UID can be entered')
            if 'view_id' in step and (not isinstance(step['view_id'],str) or not 1<=len(step['view_id'])<=256):fail(400,'Invalid view ID')
        if action=='capture':
            if not isinstance(step.get('label'),str) or not re.fullmatch('[A-Za-z0-9_:-]{1,128}',step['label']):fail(400,'Invalid capture label')
            captures+=1;matches+=int(step['label'].startswith('match_'))
    if captures>25 or matches>20:fail(400,'Capture limit exceeded')
    return steps


def safe_plan(job):
    if type(job.max_matches) is not int or not 1<=job.max_matches<=20:fail(400,'max_matches must be 1..20')
    # No invented search coordinates. Only inspect the real game screen.
    steps=[{'action':'launch_game'},{'action':'wait','ms':1500},{'action':'capture','label':'current_screen'},{'action':'done'}]
    validate_steps(steps)
    return {'id':job.id,'player_id':job.uid,'mode':job.mode,'max_matches':job.max_matches,'steps':steps}


def claim(db,device,body):
    device=db.scalar(select(c.Device).where(c.Device.id==device.id).with_for_update())
    if device.bound_device_id is None:
        other=db.scalar(select(c.Device.id).where(c.Device.bound_device_id==body.device_id,c.Device.id!=device.id))
        if other:fail(403,'Device is already registered; owner must rotate its existing token')
        device.bound_device_id=body.device_id
    elif device.bound_device_id!=body.device_id:fail(403,'Token belongs to another registered device')
    try:db.flush()
    except IntegrityError:
        db.rollback();fail(403,'Device is already registered to another principal')
    device.last_seen_at=m.utcnow()
    expire_leases(db);materialize_night(db)
    owned=db.scalar(select(c.Lease).join(c.Job,c.Job.active_lease_id==c.Lease.id).where(c.Lease.device_id==device.id,c.Lease.run_id==body.run_id,c.Lease.status=='active').with_for_update().limit(1))
    if owned:db.commit();return owned.plan
    query=select(c.Job).where(c.Job.status=='queued',c.Job.uid==get_settings().collector_pilot_uid).order_by(c.Job.created_at).with_for_update(skip_locked=True)
    for job in db.scalars(query):
        # The APK has no lease-id field. Never reassign an expired job to the
        # same run: otherwise delayed old sequence 0 could impersonate a new lease.
        if db.scalar(select(c.Lease.id).where(c.Lease.job_id==job.id,c.Lease.device_id==device.id,c.Lease.run_id==body.run_id)):continue
        plan=safe_plan(job)
        lease=c.Lease(id=new_id(),job_id=job.id,device_id=device.id,run_id=body.run_id,
                      expires_at=m.utcnow()+timedelta(seconds=get_settings().collector_lease_seconds),steps_issued=len(plan['steps']),plan=plan)
        db.add(lease);db.flush();job.active_lease_id=lease.id;job.status='leased'
        state=db.get(c.ProfileState,(job.uid,job.mode));state.status='processing';state.last_attempt_at=m.utcnow()
        db.commit();return plan
    db.commit();return None


def owned_lease(db,job_id,run_id,device,allow_complete=False):
    job=db.scalar(select(c.Job).where(c.Job.id==job_id).with_for_update())
    if job is None:fail(404,'Unknown job')
    lease=db.get(c.Lease,job.active_lease_id) if job.active_lease_id else None
    if lease is None or lease.device_id!=device.id or lease.run_id!=run_id:fail(403,'Wrong device or run lease')
    if aware(lease.expires_at)<=m.utcnow():fail(409,'Lease expired; start a new run')
    if lease.status!='active' and not (allow_complete and lease.status=='completed'):fail(409,'Lease is not active')
    return job,lease


def complete(db,device,job_id,body):
    job,lease=owned_lease(db,job_id,body.run_id,device,allow_complete=True)
    value={'status':body.status,'frame_count':body.frame_count}
    if lease.status=='completed':
        if lease.completion!=value:fail(409,'Conflicting completion')
        return {'ok':True}
    if body.frame_count!=lease.frame_count:fail(400,'frame_count mismatch')
    if body.status=='captured' and not body.frame_count:fail(400,'captured requires an accepted frame')
    lease.status='completed';lease.completion=value;job.completed_at=m.utcnow()
    job.status='captured' if body.status=='captured' else 'failed'
    # Captured is only transport success. No profile timestamp or ratings change.
    state=db.get(c.ProfileState,(job.uid,job.mode));state.status='partial';state.error_code='CALIBRATION_REQUIRED' if body.status=='captured' else 'DEVICE_FAILED'
    db.commit();return {'ok':True}
