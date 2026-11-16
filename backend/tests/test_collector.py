import base64
from datetime import datetime,timedelta,timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest
from sqlalchemy import select,func
from fastapi import HTTPException

from app import collector,collector_models as c,models as m
from app.config import get_settings
from app.collector_api import MAX_BODY
from tests.helpers import login

TOKEN='collector_test_only_'+'x'*32
OTHER='collector_test_only_'+'y'*32
HEADERS={'Authorization':'Bearer '+TOKEN}


@pytest.fixture
def enabled(monkeypatch,tmp_path,db_factory):
    settings=get_settings()
    monkeypatch.setattr(settings,'collector_enabled',True)
    monkeypatch.setattr(settings,'collector_schedule_enabled',False)
    monkeypatch.setattr(settings,'collector_storage',str(tmp_path/'frames'))
    monkeypatch.setattr(settings,'collector_pilot_uid','202777998')
    from app import collector_startup
    monkeypatch.setattr(collector_startup,'inspect_startup',lambda raw:{'screen_type':'unknown'})
    with db_factory() as db:
        db.add(c.Device(id='owner',label='test fixture',token_hash=collector.token_hash(TOKEN),revoked=False))
        db.add(c.Device(id='other',label='other fixture',token_hash=collector.token_hash(OTHER),revoked=False))
        collector.enqueue(db,'202777998','competitive','test-one','owner_test');db.commit()


def claim(client,run='run_1',device='android_uuid',headers=HEADERS):
    return client.post('/api/collector/v1/jobs/claim',headers=headers,json={'device_id':device,'run_id':run,'protocol':1,'capabilities':sorted(collector.CAPABILITIES)})


def frame_body(color='black',run='run_1'):
    buffer=BytesIO();Image.new('RGB',(900,400),color).save(buffer,format='PNG');raw=buffer.getvalue()
    return dict(run_id=run,sequence=0,label='startup_0',width=900,height=400,image_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest(),package_name='com.axlebolt.standoff2',captured_at=m.utcnow().isoformat())


def upload(client,job,body=None,headers=HEADERS):
    return client.post(f'/api/collector/v1/jobs/{job}/frames',headers=headers,json=body or frame_body())


def test_protocol_health_separate_authority_revocation(client,db_factory,enabled):
    assert client.get('/api/collector/v1/health').status_code==401
    assert client.get('/api/collector/v1/health',headers=HEADERS).json()=={'ok':True,'protocol':1}
    login(client)
    assert client.get('/api/collector/v1/health').status_code==401
    assert client.get('/api/collector/v1/health',headers={'Authorization':'Bearer '+get_settings().bot_token}).status_code==401
    with db_factory() as db:db.get(c.Device,'owner').revoked=True;db.commit()
    assert client.get('/api/collector/v1/health',headers=HEADERS).status_code==401


def test_device_binding_claim_idempotency_and_other_device_exclusion(client,db_factory,enabled):
    first=claim(client);assert first.status_code==200
    assert claim(client).json()==first.json()
    assert first.json()['max_matches']==20
    assert all(set(step).issuperset({'action'}) and 'type' not in step for step in first.json()['steps'])
    assert claim(client,device='wrong_uuid').status_code==403
    assert claim(client,device='android_uuid',headers={'Authorization':'Bearer '+OTHER}).status_code==403
    assert claim(client,device='second_uuid',headers={'Authorization':'Bearer '+OTHER}).status_code==204
    with db_factory() as db:
        assert db.get(c.Device,'owner').bound_device_id=='android_uuid'
        assert db.scalar(select(func.count()).select_from(c.Lease))==1


def test_frame_dedup_hash_and_complete_are_not_profile_success(client,db_factory,enabled):
    job=claim(client).json()['id'];body=frame_body()
    assert upload(client,job,body).json()=={'ok':True,'next_steps':[]}
    assert upload(client,job,body).json()=={'ok':True,'next_steps':[]}
    assert upload(client,job,frame_body('white')).status_code==409
    completion=dict(run_id='run_1',status='captured',reason='transport only',frame_count=1)
    assert client.post(f'/api/collector/v1/jobs/{job}/complete',headers=HEADERS,json=completion).json()=={'ok':True}
    assert client.post(f'/api/collector/v1/jobs/{job}/complete',headers=HEADERS,json=completion).json()=={'ok':True}
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(c.Frame))==1
        state=db.get(c.ProfileState,('202777998','competitive'))
        assert state.last_success_at is None and state.status=='partial'
        assert db.scalar(select(func.count()).select_from(m.Match))==0


def test_expired_lease_preserves_namespace_resets_sequence_rejects_old_run(client,db_factory,enabled):
    job=claim(client).json()['id'];assert upload(client,job).status_code==200
    with db_factory() as db:
        old=db.scalar(select(c.Lease));old.expires_at=m.utcnow()-timedelta(seconds=1);old_id=old.id;db.commit()
    assert claim(client).status_code==204  # same run cannot inherit a new lease
    assert claim(client,run='run_2').json()['id']==job
    assert upload(client,job).status_code==403
    assert upload(client,job,frame_body('white',run='run_2')).status_code==200
    with db_factory() as db:
        frames=list(db.scalars(select(c.Frame)))
        assert len(frames)==2 and frames[0].sequence==frames[1].sequence==0
        assert frames[0].lease_id!=frames[1].lease_id
        assert all((Path(get_settings().collector_storage)/f.storage_path).is_file() for f in frames)


@pytest.mark.parametrize('mutate,status',[
    (lambda b:b.update(run_id='wrong_run'),403),
    (lambda b:b.update(sha256='0'*64),400),
    (lambda b:b.update(width=899),400),
    (lambda b:b.update(image_base64='not base64!'),400),
    (lambda b:b.update(sequence=25),400),
    (lambda b:b.update(package_name='some.other.app'),400),
    (lambda b:b.update(sequence=1),409),
    (lambda b:b.update(label='not_requested'),409),
    (lambda b:b.update(width=16384,height=16384),413),
])
def test_invalid_frames_cannot_mutate_state(client,db_factory,enabled,mutate,status):
    job=claim(client).json()['id'];body=frame_body();mutate(body)
    assert upload(client,job,body).status_code==status
    with db_factory() as db:assert db.scalar(select(func.count()).select_from(c.Frame))==0


def test_oversized_body_does_not_echo_data(client,enabled):
    response=client.post('/api/collector/v1/jobs/claim',headers={**HEADERS,'Content-Length':str(MAX_BODY+1),'Content-Type':'application/json'},content='{}')
    assert response.status_code==413
    response=client.post('/api/collector/v1/events',headers=HEADERS,json={'pattern':'never log this','run_id':'r'})
    assert response.status_code==400 and 'never log this' not in response.text


@pytest.mark.parametrize('steps',[
    [{'type':'tap','x':.5,'y':.5}], [{'action':'shell','command':'anything'}],
    [{'action':'launch_game','package':'other.app'}], [{'action':'tap','x':float('nan'),'y':0}],
    [{'action':'wait','ms':10001}], [{'action':'set_text','text':'https://example.test'}],
    [{'action':'done'}]*121, [{'action':'capture','label':f'match_{i}'} for i in range(21)],
    [{'action':'capture','label':f'nav_{i}'} for i in range(26)],
])
def test_action_grammar_and_total_limits(steps):
    with pytest.raises(HTTPException):collector.validate_steps(steps)


def test_events_are_operational_only_free_text_never_persisted(client,db_factory,enabled):
    for event in ['LOCKED','verified','unrecognized_event']:
        assert client.post('/api/collector/v1/events',headers=HEADERS,json={'run_id':'r','event':event,'at':m.utcnow().isoformat(),'message':TOKEN+' local secret'}).json()=={'ok':True}
    with db_factory() as db:
        codes=list(db.scalars(select(c.Event.code)))
        assert codes==['LOCKED','OTHER','OTHER']
        assert db.get(c.ProfileState,('202777998','competitive')).last_success_at is None


def test_shared_primary_uid_pending_without_fake_player_and_search_does_not_rebind(client,db_factory,enabled):
    login(client,100)
    result=client.put('/api/me',json={'primary_uid':'202777998'})
    assert result.status_code==200 and result.json()['primary_uid']=='202777998'
    page=client.get('/api/players/202777998?mode=allies').json()
    assert page['data_status']=='pending' and page['player']['nickname'] is None
    assert page['summary']['rating'] is None and page['collector_status']['last_success_at'] is None
    assert client.get('/api/players/123456789').status_code==200
    assert client.get('/api/me').json()['primary_uid']=='202777998'
    login(client,101)
    assert client.put('/api/me',json={'primary_uid':'202777998'}).status_code==200
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.Player))==0
        assert db.scalar(select(func.count()).select_from(c.Job).where(c.Job.uid=='202777998',c.Job.mode=='competitive'))==1
        assert db.get(m.User,100).preferred_uid==db.get(m.User,101).preferred_uid=='202777998'


def test_moscow_midnight_and_failed_capture_keep_actual_success_time(client,db_factory,enabled):
    before=datetime(2026,11,16,20,59,59,tzinfo=timezone.utc)
    after=datetime(2026,11,16,21,0,0,tzinfo=timezone.utc)
    assert collector.next_night(before).isoformat()=='2026-11-17T00:00:00+03:00'
    assert collector.next_night(after).isoformat()=='2026-11-18T00:00:00+03:00'
    previous=m.utcnow()-timedelta(days=2)
    with db_factory() as db:
        db.get(c.ProfileState,('202777998','competitive')).last_success_at=previous;db.commit()
    job=claim(client).json()['id']
    assert client.post(f'/api/collector/v1/jobs/{job}/complete',headers=HEADERS,json={'run_id':'run_1','status':'failed','frame_count':0,'reason':'LOCKED'}).status_code==200
    with db_factory() as db:
        status=collector.profile_status(db,'202777998','competitive')
        assert status['last_success_at']==previous.isoformat() and status['status']=='partial'


def test_manual_import_flag_enforced_at_storage_boundary(db,monkeypatch):
    from app.personal_matches import save_screenshot_match
    from tests.test_personal_matches import board
    monkeypatch.setattr(get_settings(),'manual_import_enabled',False)
    with pytest.raises(ValueError,match='отключён'):save_screenshot_match(db,100,board())


def test_worker_restart_does_not_retry_oom_or_interrupted_frames_forever(client,db_factory,enabled,monkeypatch):
    from app import collector_worker
    monkeypatch.setattr(collector_worker,'SessionLocal',db_factory)
    job=claim(client).json()['id'];assert upload(client,job).status_code==200
    with db_factory() as db:
        frame=db.scalar(select(c.Frame));frame.status='processing';frame.analysis={'attempts':2};db.commit()
    collector_worker.recover_and_schedule()
    with db_factory() as db:
        frame=db.scalar(select(c.Frame));assert frame.status=='needs_review'
        assert frame.analysis['reason']=='INTERRUPTED_RETRY_LIMIT'


def test_loading_is_polled_dynamically_and_retries_do_not_issue_more_steps(client,db_factory,enabled,monkeypatch):
    from app import collector_startup
    monkeypatch.setattr(collector_startup,'inspect_startup',lambda raw:{'screen_type':'loading'})
    plan=claim(client).json();job=plan['id']
    assert [x['ms'] for x in plan['steps'] if x['action']=='wait']==[10000,10000]
    for sequence in range(5):
        body=frame_body();body.update(sequence=sequence,label=f'startup_{sequence}')
        first=upload(client,job,body)
        assert first.status_code==200 and upload(client,job,body).json()==first.json()
        more=first.json()['next_steps']
        if sequence<4:
            assert more==[{'action':'wait','ms':10000},{'action':'wait','ms':10000},
                          {'action':'capture','label':f'startup_{sequence+1}'}]
        else:assert more==[]
    with db_factory() as db:
        lease=db.scalar(select(c.Lease));assert lease.frame_count==5 and lease.steps_issued==17
        collector.validate_steps(lease.plan['steps'])
        frame=db.scalar(select(c.Frame).where(c.Frame.sequence==4))
        assert frame.analysis['reason']=='GAME_LOAD_TIMEOUT'
        assert db.get(c.ProfileState,('202777998','competitive')).last_success_at is None
    assert client.post(f'/api/collector/v1/jobs/{job}/complete',headers=HEADERS,json={
        'run_id':'run_1','status':'captured','frame_count':5}).status_code==200
    with db_factory() as db:
        assert db.get(c.Job,job).status=='partial'
        assert db.get(c.ProfileState,('202777998','competitive')).error_code=='GAME_LOAD_TIMEOUT'


@pytest.mark.parametrize('screen,reason',[('unknown','CALIBRATION_REQUIRED'),('profile_candidate','CALIBRATION_REQUIRED'),('blank','WAITING_FOR_GAME')])
def test_startup_never_taps_an_unverified_screen(client,db_factory,enabled,monkeypatch,screen,reason):
    from app import collector_startup
    monkeypatch.setattr(collector_startup,'inspect_startup',lambda raw:{'screen_type':screen})
    job=claim(client).json()['id'];response=upload(client,job).json()
    assert all(s['action'] in {'wait','capture'} for s in response['next_steps'])
    with db_factory() as db:assert db.scalar(select(c.Frame)).analysis['reason']==reason


def test_black_frame_probe_needs_no_tesseract():
    import subprocess,sys
    payload=base64.b64decode(frame_body()['image_base64'])
    process=subprocess.run([sys.executable,'-m','app.collector_vision_worker'],input=payload,cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=10)
    assert process.returncode==0
    assert json.loads(process.stdout)['screen_type']=='blank'


def test_observed_loading_artwork_probe_when_private_frame_available():
    import subprocess,sys
    root=Path(__file__).resolve().parents[2]/'.secrets'
    paths=[root/name for name in ['collector-second-frame.jpg','collector-menu-frame.jpg']]
    if not all(path.exists() for path in paths):pytest.skip('Owner launch frames are private')
    for path in paths:
        payload=path.read_bytes()
        process=subprocess.run([sys.executable,'-m','app.collector_vision_worker'],input=payload,cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=10)
        assert process.returncode==0
        result=json.loads(process.stdout)
        assert result['screen_type']=='loading' and result['recognition']=='observed_launch_artwork'


def test_blank_and_other_images_are_not_known_loading_artwork():
    from app.collector_screen import known_loading_artwork
    assert not known_loading_artwork(Image.new('RGB',(900,400),'black'))
    assert not known_loading_artwork(Image.new('RGB',(900,400),'white'))
