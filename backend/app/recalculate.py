"""Explicitly migrate all saved raw K/D/A/round inputs to the current method."""
from sqlalchemy import select

from .db import SessionLocal
from .models import Participant, ScreenshotMatch, User, Match, utcnow
from .rating import VERSION, calculate_match as calculate, formula_version
from .personal_matches import _screenshot_snapshot, get_personal_best


def run(mode=None):
    count = 0
    with SessionLocal() as db:
        query = select(Participant)
        if mode:
            query = query.where(Participant.mode == mode)
        for row in db.scalars(query.execution_options(yield_per=1000)):
            match = db.get(Match, row.match_id)
            rating = calculate(row.mode, row.kills, row.deaths, row.assists, row.rounds, score=match.score)
            row.rating = rating["value"]
            row.rating_tier = rating["tier"]
            row.formula_version = formula_version(row.mode)
            row.rating_inputs = rating["inputs"]
            row.rating_parameters = rating["parameters"]
            row.rating_factors = rating["factors"]
            count += 1
        shots = select(ScreenshotMatch)
        if mode:
            shots = shots.where(ScreenshotMatch.mode == mode)
        shot_count = 0
        for shot in db.scalars(shots.execution_options(yield_per=500)):
            shot.snapshot = _screenshot_snapshot(shot)
            shot.rating = calculate(shot.mode, shot.kills, shot.deaths, shot.assists,
                                    rounds=shot.snapshot.get('personal_rounds'), score=shot.snapshot.get('score'))['value']
            shot.formula_version = formula_version(shot.mode)
            shot.updated_at = utcnow()
            shot_count += 1
        db.flush()
        for user in db.scalars(select(User)):
            for selected_mode in ([mode] if mode else ['competitive', 'allies']):
                get_personal_best(db, user, selected_mode)
        db.commit()
    print(f"Recalculated {count} participant and {shot_count} screenshot records; personal bests refreshed")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['competitive', 'allies'])
    run(parser.parse_args().mode)
