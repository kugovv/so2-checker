"""Explicitly migrate all saved raw K/D/A/round inputs to the current method."""
from sqlalchemy import select

from .db import SessionLocal
from .models import Participant
from .rating import VERSION, calculate


def run():
    count = 0
    with SessionLocal() as db:
        for row in db.scalars(select(Participant).execution_options(yield_per=1000)):
            rating = calculate(row.mode, row.kills, row.deaths, row.assists, row.rounds)
            row.rating = rating["value"]
            row.rating_tier = rating["tier"]
            row.formula_version = VERSION
            row.rating_inputs = rating["inputs"]
            row.rating_parameters = rating["parameters"]
            row.rating_factors = rating["factors"]
            count += 1
        db.commit()
    print(f"Recalculated {count} participant records with {VERSION}")


if __name__ == "__main__":
    run()
