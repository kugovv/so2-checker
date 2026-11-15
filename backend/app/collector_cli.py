"""Owner-only local provisioning; never prints tokens or receives a pattern lock."""
import argparse,json,re,sys
from pathlib import Path
from sqlalchemy import select
from .db import SessionLocal
from . import collector,collector_models as c


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['register-device','revoke-device','enqueue-test','status'])
    parser.add_argument('--device',default='')
    parser.add_argument('--uid',default='202777998')
    parser.add_argument('--mode',choices=['competitive','allies'],default='competitive')
    args=parser.parse_args()
    with SessionLocal() as db:
        if args.command=='register-device':
            value=json.load(sys.stdin);token=value.get('collector_token','')
            if not re.fullmatch('[A-Za-z0-9_-]{32,128}',token):raise SystemExit('Invalid device token format')
            digest=collector.token_hash(token)
            device=db.get(c.Device,args.device) if args.device else db.scalar(select(c.Device).where(c.Device.token_hash==digest))
            if args.device and device is None:raise SystemExit('Unknown device principal')
            if not device:
                device=c.Device(id=collector.new_id(),label='Owner Android / TECNO POVA 6',token_hash=digest,revoked=False)
                db.add(device)
            else:
                device.token_hash=digest;device.revoked=False
            db.commit();print('Registered device principal:',device.id)
        elif args.command=='revoke-device':
            device=db.get(c.Device,args.device)
            if device is None:raise SystemExit('Unknown device principal')
            device.revoked=True;db.commit();print('Device token revoked')
        elif args.command=='enqueue-test':
            job=collector.enqueue(db,args.uid,args.mode,'test:'+collector.new_id(),origin='owner_test')
            db.commit();print('Safe calibration capture queued:',job.id,args.uid,args.mode)
        else:
            from sqlalchemy import func
            for model,field in [(c.Job,c.Job.status),(c.Frame,c.Frame.status),(c.Candidate,c.Candidate.verification_status)]:
                print(model.__tablename__,list(db.execute(select(field,func.count()).group_by(field))))
            print('Profiles successfully verified:',db.scalar(select(func.count()).select_from(c.ProfileState).where(c.ProfileState.last_success_at.is_not(None))))
            print('Devices (no secrets):',list(db.execute(select(c.Device.id,c.Device.label,c.Device.bound_device_id,c.Device.revoked))))


if __name__=='__main__':main()
