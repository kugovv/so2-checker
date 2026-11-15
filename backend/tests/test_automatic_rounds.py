from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import models as m, personal_matches as personal, recalculate
from app.rating import VERSION, calculate_match, displayed, match_round_count
from app.service import ingest_page, upsert_profile
from app.source import HistoryPage, SourceUnavailable
from tests.helpers import match, profile
from tests.test_personal_matches import board


def test_score_8_6_uses_fourteen_not_twenty_eight_and_is_not_personal_evidence():
    result=calculate_match('competitive',22,7,0,score=[8,6])
    assert result['inputs']['rounds']==14
    assert result['tier']=='match_rounds'
    assert result['parameters']['rounds_basis']=='match_total_assumed_full'
    assert result['value']==Decimal('1.78061224')


@pytest.mark.parametrize('score',[None,[],[8],['8',6],[True,6],[-1,15],[0,0],[200,1]])
def test_missing_invalid_score_never_becomes_zero_or_assumed_personal_rounds(score):
    result=calculate_match('competitive',22,7,0,score=score)
    assert result['inputs']['rounds'] is None
    assert displayed(result['value'])=='1.34' and result['tier']=='match'
    assert result['parameters']['rounds_basis']=='unknown'


def test_known_personal_rounds_take_precedence_and_allies_unchanged():
    result=calculate_match('competitive',10,7,2,rounds=10,score=[8,6])
    assert result['inputs']['rounds']==10 and result['tier']=='rounds'
    assert result['parameters']['rounds_basis']=='personal'
    assert calculate_match('allies',13,3,0,score=[8,3])['value']==Decimal('1.85479031')


def test_impossible_score_kda_combination_uses_labeled_fallback():
    for k,d in [(22,15),(71,7)]:
        result=calculate_match('competitive',k,d,0,score=[8,6])
        assert result['tier']=='match' and result['inputs']['rounds'] is None


def test_saved_archive_recalculates_automatically_without_claiming_confirmation(db_factory,monkeypatch):
    with db_factory() as db:
        saved=personal.save_screenshot_match(db,100,board())
        row=db.get(m.ScreenshotMatch,saved['match']['screenshot_id'])
        row.rating=Decimal('1.33666667');row.formula_version='so2-v1-preliminary'
        snapshot=deepcopy(row.snapshot);snapshot.update(rating=1.33666667,rating_display='1.34',rating_tier='match')
        row.snapshot=snapshot
        db.get(m.PersonalBest,(100,'competitive')).formula_version='so2-v1-preliminary'
        db.commit()
    monkeypatch.setattr(recalculate,'SessionLocal',db_factory)
    recalculate.run('competitive')
    with db_factory() as db:
        row=db.scalar(select(m.ScreenshotMatch))
        assert row.rating==Decimal('1.78061224') and row.formula_version==VERSION
        assert row.snapshot['personal_rounds'] is None
        assert row.snapshot['rating_inputs']['rounds']==14
        assert row.snapshot['rounds_basis']=='match_total_assumed_full'
        assert personal.get_personal_best(db,db.get(m.User,100),'competitive')['rating_display']=='1.78'
        repeated=personal.save_screenshot_match(db,100,board())
        assert repeated['match']['screenshot_id']==row.id


def test_automatic_source_uses_score_without_fabricating_personal_round_field(db):
    player=upsert_profile(db,profile())
    item=match('score-only',kills=22,deaths=7,assists=0,rounds=None);item.score=[8,6]
    ingest_page(db,player.uid,'competitive',HistoryPage([item]))
    person=db.scalar(select(m.Participant))
    assert person.rounds is None and person.rating==Decimal('1.78061224')
    assert person.rating_tier=='match_rounds'
    assert person.rating_inputs['rounds']==14
    assert person.rating_parameters['rounds_basis']=='match_total_assumed_full'
    user=personal.ensure_personal_user(db,100);user.primary_uid=player.uid
    best=personal.get_personal_best(db,user,'competitive')
    assert best['source']=='automatic' and best['rating_display']=='1.78'


def test_source_rejects_malformed_score_before_storing_ambiguous_rounds(db):
    player=upsert_profile(db,profile())
    item=match('bad-score');item.score=['8',6]
    with pytest.raises(SourceUnavailable): ingest_page(db,player.uid,'competitive',HistoryPage([item]))
