from decimal import Decimal
import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.rating import ALLIES_VERSION, VERSION, calculate, displayed, formula_version, rating_color
from app import models as m, personal_matches as personal
from app import recalculate
from tests.test_personal_matches import board
from tests.test_screenshot_bot import harness, FakeMessage, click


@pytest.mark.parametrize('kills,deaths,assists,expected', [
    (0,0,0,'0.66'),(1,0,0,'1.00'),(1,10,2,'0.41'),(4,5,2,'0.88'),
    (8,8,2,'0.98'),(10,5,0,'1.31'),(13,3,0,'1.85'),(15,5,0,'1.64'),(18,3,0,'2.23')])
def test_allies_scale(kills,deaths,assists,expected):
    result = calculate('allies',kills,deaths,assists)
    assert displayed(result['value']) == expected
    assert result['tier'] == 'duels'
    assert formula_version('allies') == ALLIES_VERSION != VERSION


def test_allies_monotonicity_finite_zero_deaths_missing_and_no_double_reward():
    for k in range(0,23):
        for d in range(0,15):
            base=calculate('allies',k,d,0)['value']
            assert base.is_finite()
            assert calculate('allies',k+1,d,0)['value'] > base
            assert calculate('allies',k,d+1,0)['value'] < base
            assert calculate('allies',k,d,1)['value'] > base
    assert calculate('allies',13,3,None)['value'] is None
    assert calculate('allies',13,3,0,11)['value'] == calculate('allies',13,3,0)['value']
    assert rating_color(calculate('allies',13,3,0)['value']) == 'gold'
    assert displayed(calculate('competitive',22,7,0,14)['value']) == '1.78'
    assert displayed(calculate('competitive',22,7,0)['value']) == '1.34'


def test_confirmed_rounds_persist_only_for_own_row_and_deduplicate(db):
    payload=board()
    old=personal.save_screenshot_match(db,100,payload)
    payload['personal_rounds']=14
    saved=personal.save_screenshot_match(db,100,payload)
    assert saved['match']['id']==old['match']['id']
    assert saved['match']['rating_display']=='1.78'
    assert saved['best']['rating_tier']=='rounds'
    assert saved['best']['personal_rounds']==14
    assert saved['match']['players'][1]['rating']==old['match']['players'][1]['rating']
    # A larger fallback value cannot silently replace a rounds-based record.
    other=personal.save_screenshot_match(db,100,board('unknown',kills=40,deaths=1,assists=10) | {'score':None})
    assert other['best']['id']==saved['best']['id']
    assert personal.get_screenshot_match(db,db.get(m.User,100),saved['match']['screenshot_id'])['rating_display']=='1.78'


@pytest.mark.parametrize('rounds',[0,-1,True,15,201])
def test_invalid_personal_round_confirmation_rejected(db,rounds):
    payload=board();payload['personal_rounds']=rounds
    with pytest.raises(ValueError): personal.save_screenshot_match(db,100,payload)


def test_old_archive_recalculated_and_best_uses_current_duel_sql(db_factory,monkeypatch):
    with db_factory() as db:
        a=personal.save_screenshot_match(db,100,board('high-duel',mode='allies',kills=13,deaths=3,assists=0))
        b=personal.save_screenshot_match(db,100,board('high-volume',mode='allies',kills=22,deaths=14,assists=0))
        competitive=personal.save_screenshot_match(db,100,board('competitive'))
        for row in db.scalars(select(m.ScreenshotMatch).where(m.ScreenshotMatch.mode=='allies')):
            row.rating=99;row.formula_version='old'
        db.get(m.PersonalBest,(100,'allies')).formula_version='old'
        db.commit()
    monkeypatch.setattr(recalculate,'SessionLocal',db_factory)
    recalculate.run('allies')
    with db_factory() as db:
        shots=list(db.scalars(select(m.ScreenshotMatch).where(m.ScreenshotMatch.mode=='allies')))
        assert all(row.formula_version==ALLIES_VERSION for row in shots)
        best=personal.get_personal_best(db,db.get(m.User,100),'allies')
        assert best['match_id']=='high-duel' and best['rating_display']=='1.85'
        assert personal.get_personal_best(db,db.get(m.User,100),'competitive')['rating_display']=='1.78'


def test_remembered_nickname_private_durable_and_only_after_confirmation(db_factory):
    with db_factory() as db:
        assert personal.get_screenshot_nickname(db,100) is None
        personal.save_screenshot_match(db,100,board(nickname='harny'))
        db.commit()
    with db_factory() as db:
        assert personal.get_screenshot_nickname(db,100)=='harny'
        assert personal.get_screenshot_nickname(db,101) is None
        personal.save_screenshot_match(db,100,board('next',nickname='newname'))
        db.rollback()
    with db_factory() as db: assert personal.get_screenshot_nickname(db,100)=='harny'


def test_bot_automatically_uses_score_without_participation_question():
    async def scenario():
        saved=[]
        async def saver(uid,payload):
            saved.append(payload);return {'is_new_best':False}
        async with harness(saver=saver) as (c,state,bot,_):
            message=FakeMessage(caption='hamy')
            await c.upload(message,state,bot)
            assert (await state.get_data()).get('personal_rounds') is None
            assert '14 раундов матча' in message.answers[-1][0]
            markup=message.answers[-1][1]['reply_markup']
            assert not any(':rounds:' in (b.callback_data or '') for row in markup.inline_keyboard for b in row)
            await click(c,state,message,'confirm')
            assert 'Рейтинг за матч: 1.78' in message.answers[-1][0]
            assert saved[0]['personal_rounds'] is None
            assert saved[0]['score']==[8,6]
            await c.upload(message,state,bot)
            await click(c,state,message,'manual')
            assert (await state.get_data()).get('personal_rounds') is None
    asyncio.run(scenario())


def test_bot_reuses_nickname_but_asks_again_if_missing():
    async def scenario():
        async with harness() as (c,state,bot,_):
            async def nickname(uid): assert uid==91;return 'hamy'
            c.nickname_loader=nickname
            message=FakeMessage()
            await c.upload(message,state,bot)
            assert (await state.get_data())['phase']=='confirm'
            assert (await state.get_data())['requested_nickname']=='hamy'
            await c.cancel(message,state)
            async def absent(uid): return 'someone-else'
            c.nickname_loader=absent
            await c.upload(message,state,bot)
            assert (await state.get_data())['phase']=='nickname'
            assert 'не найден' in message.answers[-1][0]
    asyncio.run(scenario())
