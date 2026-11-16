"""Exact Night Bridge protocol 1. No route is authenticated by Mini App sessions."""
import base64
import binascii
import hashlib
import hmac
from pathlib import Path
import re
import shutil
import struct

from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field,AwareDatetime,field_validator
from typing import Annotated,Literal
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from . import collector as service,collector_models as c,models as m
from .config import get_settings
from .db import get_db
from . import collector_startup

router=APIRouter(prefix='/api/collector/v1')
MAX_BODY=6*1024*1024
MAX_IMAGE=4*1024*1024
Identifier=Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')]


class Body(BaseModel):
    model_config=ConfigDict(extra='forbid')


class ClaimBody(Body):
    device_id:Identifier
    run_id:Identifier
    protocol:Literal[1]
    capabilities:list[Annotated[str,Field(max_length=64)]]=Field(max_length=16)
    @field_validator('protocol',mode='before')
    @classmethod
    def exact_protocol(cls,value):
        if type(value) is not int or value!=1:raise ValueError('protocol 1 required')
        return value


class FrameBody(Body):
    run_id:Identifier
    sequence:int=Field(strict=True,ge=0,le=24)
    label:str=Field(pattern=r'^[A-Za-z0-9_:-]{1,128}$')
    width:int=Field(strict=True,ge=1,le=16384)
    height:int=Field(strict=True,ge=1,le=16384)
    image_base64:str=Field(min_length=4,max_length=MAX_IMAGE*4//3+8)
    sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    package_name:Literal['com.axlebolt.standoff2']
    captured_at:AwareDatetime


class CompleteBody(Body):
    run_id:Identifier
    status:Literal['captured','failed']
    reason:str=Field(default='',max_length=512)
    frame_count:int=Field(strict=True,ge=0,le=25)


class EventBody(Body):
    run_id:Identifier
    event:str=Field(min_length=1,max_length=64)
    message:str=Field(default='',max_length=512)
    at:AwareDatetime


def device_auth(request:Request,db:Session=Depends(get_db)):
    if not get_settings().collector_enabled:service.fail(503,'Collector is not enabled')
    header=request.headers.get('authorization','')
    token=header[7:] if header.startswith('Bearer ') else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token,re.ASCII):service.fail(401,'Dedicated collector token required')
    device=db.scalar(select(c.Device).where(c.Device.token_hash==service.token_hash(token),c.Device.revoked.is_(False)))
    if device is None:service.fail(401,'Invalid or revoked collector token')
    return device


@router.get('/health')
def health(device=Depends(device_auth),db:Session=Depends(get_db)):
    device.last_seen_at=m.utcnow();db.commit()
    return {'ok':True,'protocol':1}


@router.post('/jobs/claim')
def claim(body:ClaimBody,device=Depends(device_auth),db:Session=Depends(get_db)):
    if not service.CAPABILITIES.issubset(set(body.capabilities)):service.fail(400,'Required APK capabilities missing')
    plan=service.claim(db,device,body)
    return plan if plan else Response(status_code=204)


def image_dimensions(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24 and data[12:16]==b'IHDR':
        return (*struct.unpack('>II',data[16:24]),'.png')
    if data.startswith(b'\xff\xd8'):
        offset=2
        while offset+4<=len(data):
            if data[offset]!=255:offset+=1;continue
            while offset<len(data) and data[offset]==255:offset+=1
            if offset>=len(data):break
            marker=data[offset];offset+=1
            if marker in (0xD8,0xD9,0x01) or 0xD0<=marker<=0xD7:continue
            if offset+2>len(data):break
            length=struct.unpack('>H',data[offset:offset+2])[0]
            if length<2 or offset+length>len(data):break
            if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF) and length>=7:
                height,width=struct.unpack('>HH',data[offset+3:offset+7]);return width,height,'.jpg'
            offset+=length
    service.fail(400,'Expected a JPEG or PNG image')


@router.post('/jobs/{job_id}/frames')
def frame(job_id:str,body:FrameBody,device=Depends(device_auth),db:Session=Depends(get_db)):
    job,lease=service.owned_lease(db,job_id,body.run_id,device)
    if body.width*body.height>12_000_000:service.fail(413,'Image pixel limit exceeded')
    try:raw=base64.b64decode(body.image_base64,validate=True)
    except (ValueError,binascii.Error):service.fail(400,'Invalid image encoding')
    if len(raw)>MAX_IMAGE:service.fail(413,'Image byte limit exceeded')
    digest=hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest,body.sha256):service.fail(400,'Image hash mismatch')
    width,height,extension=image_dimensions(raw)
    if (width,height)!=(body.width,body.height):service.fail(400,'Image dimensions mismatch')
    existing=db.scalar(select(c.Frame).where(c.Frame.lease_id==lease.id,c.Frame.sequence==body.sequence))
    if existing:
        if existing.sha256!=digest or existing.label!=body.label:service.fail(409,'Conflicting frame sequence')
        return existing.response
    captures=[step['label'] for step in lease.plan['steps'] if step['action']=='capture']
    if body.sequence!=lease.frame_count or body.sequence>=len(captures) or captures[body.sequence]!=body.label:
        service.fail(409,'Frame was not requested by the current plan')
    if lease.frame_count>=25:service.fail(400,'Frame limit exceeded')
    if body.label.startswith('match_') and lease.match_frame_count>=min(20,job.max_matches):service.fail(400,'Match capture limit exceeded')
    config=get_settings();root=Path(config.collector_storage).resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    used=db.scalar(select(func.coalesce(func.sum(c.Frame.byte_size),0)))
    if used+len(raw)>config.collector_max_storage_mb*1024*1024 or shutil.disk_usage(root).free<512*1024*1024:
        service.fail(507,'Collector evidence storage limit reached')
    relative=Path(job.id)/'runs'/lease.id/'frames'/f'{body.sequence:03d}_{digest[:16]}{extension}'
    path=(root/relative).resolve()
    if not path.is_relative_to(root):service.fail(400,'Invalid frame path')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Exclusive write: a successful retry is resolved from the DB record above.
    try:
        with path.open('xb') as output:output.write(raw)
        path.chmod(0o600)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:service.fail(409,'Conflicting stored frame')
    startup=bool(re.fullmatch(r'startup_[0-4]',body.label))
    analysis=collector_startup.inspect_startup(raw) if startup else {}
    next_steps=collector_startup.advance(lease,body.label,analysis) if startup else []
    response={'ok':True,'next_steps':next_steps}
    db.add(c.Frame(id=service.new_id(),lease_id=lease.id,sequence=body.sequence,sha256=digest,storage_path=relative.as_posix(),
                   byte_size=len(raw),width=width,height=height,label=body.label,captured_at=body.captured_at,response=response,
                   status='needs_review' if startup else 'queued',analysis=analysis,
                   parser_version=service.PARSER_VERSION if startup else None))
    if startup:
        state=db.get(c.ProfileState,(job.uid,job.mode));state.error_code=analysis.get('reason','CALIBRATION_REQUIRED')
    lease.frame_count+=1;lease.match_frame_count+=int(body.label.startswith('match_'))
    db.commit();return response


@router.post('/jobs/{job_id}/complete')
def complete(job_id:str,body:CompleteBody,device=Depends(device_auth),db:Session=Depends(get_db)):
    return service.complete(db,device,job_id,body)


@router.post('/events')
def event(body:EventBody,device=Depends(device_auth),db:Session=Depends(get_db)):
    known={'START','CANCELLED','LOCKED','ACCESSIBILITY_UNAVAILABLE','UNEXPECTED_FOREGROUND','QUEUE_EMPTY','CAPTURE_FAILED','NETWORK_FAILED','COMPLETED','start','cancelled','locked'}
    code=body.event.upper() if body.event in known else 'OTHER'
    # Intentionally discard free-form client messages: never persist a pattern,
    # pasted token or arbitrary exception string through this endpoint.
    db.add(c.Event(id=service.new_id(),device_id=device.id,run_id=body.run_id,code=code,occurred_at=body.at))
    db.commit();return {'ok':True}
