from decimal import Decimal

from sqlalchemy import select

from app import models as m, personal_matches as personal, recalculate
from app.rating import ALLIES_VERSION, calculate_match, displayed
from app.service import ingest_page, upsert_profile
from app.source import HistoryPage
from tests.helpers import match, profile
from tests.test_personal_matches import board


def test_weak_match_has_no_win_bonus_or_round_length_multiplier():
    for score in ([8,6],[6,8],[8,0],[0,8],None):
        result=calculate_match('allies',3,11,1,score=score)
        assert result['value']==Decimal('0.33461538')
        assert result['factors']['duel_numerator']==4.35
        assert result['factors']['duel_denominator']==13
        assert result['factors']['exponent']==1.0
        assert result['parameters']['rounds_used'] is False


def test_weak_branch_not_lifted_toward_neutral_and_neutral_is_continuous():
    for k,d,a in [(0,0,0),(1,10,2),(3,11,1),(4,5,2),(8,8,2)]:
        ratio=(Decimal(k)+Decimal('.35')*a+1)/(Decimal(d)+2)
        actual=calculate_match('allies',k,d,a)
        assert actual['value']==ratio.quantize(Decimal('.00000001'))
    assert calculate_match('allies',1,0,0)['value']==1
    assert calculate_match('allies',1,0,1)['value']>1
    assert calculate_match('allies',0,0,0)['value']<1
    strong=calculate_match('allies',13,3,0)
    assert strong['value']==Decimal('1.85479031') and strong['factors']['exponent']==.6


def test_source_result_is_context_only_not_a_rating_component(db):
    player=upsert_profile(db,profile())
    for number,score,result in [('win',[8,6],'win'),('loss',[6,8],'loss')]:
        item=match(number,mode='allies',kills=3,deaths=11,assists=1,headshots=None,rounds=None)
        item.score=score;item.participants[0].result=result
        ingest_page(db,player.uid,'allies',HistoryPage([item]))
    rows=list(db.scalars(select(m.Participant)))
    assert {p.result for p in rows}=={'win','loss'}
    assert {p.rating for p in rows}=={Decimal('0.33461538')}


def test_v2_archive_recalculates_weak_matches_without_touching_competitive(db_factory,monkeypatch):
    with db_factory() as db:
        saved=personal.save_screenshot_match(db,100,board('weak',mode='allies',kills=3,deaths=11,assists=1))
        row=db.get(m.ScreenshotMatch,saved['match']['screenshot_id'])
        row.rating=Decimal('0.51847467');row.formula_version='so2-allies-v2-preliminary'
        personal.save_screenshot_match(db,100,board('competitive'))
        db.get(m.PersonalBest,(100,'allies')).formula_version='so2-allies-v2-preliminary'
        db.commit()
    monkeypatch.setattr(recalculate,'SessionLocal',db_factory)
    recalculate.run('allies')
    with db_factory() as db:
        row=db.scalar(select(m.ScreenshotMatch).where(m.ScreenshotMatch.mode=='allies'))
        assert row.rating==Decimal('0.33461538') and row.formula_version==ALLIES_VERSION
        best=personal.get_personal_best(db,db.get(m.User,100),'allies')
        assert best['rating_display']=='0.33' and best['formula_version']==ALLIES_VERSION
        assert personal.get_personal_best(db,db.get(m.User,100),'competitive')['rating_display']=='1.78'
