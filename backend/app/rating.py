"""Deterministic provisional Standoff efficiency v1, not an official game rating."""
from decimal import Decimal, ROUND_HALF_UP
from statistics import median, pstdev

VERSION = "so2-v1-preliminary"
PARAMETERS = {
    "competitive": {
        "rounds": {"kills": "0.70", "deaths": "0.70", "assists": "0.20"},
        "match": {"kills": "15", "deaths": "15", "assists": "4"},
    },
    "allies": {
        "rounds": {"kills": "0.80", "deaths": "0.80", "assists": "0.15"},
        "match": {"kills": "10", "deaths": "10", "assists": "2"},
    },
}
WEIGHTS = {"kills": "0.65", "deaths": "0.25", "assists": "0.10"}


def dec(value):
    return Decimal(str(value))


def displayed(value):
    return None if value is None else format(dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def rating_color(value):
    if value is None:
        return None
    value = dec(displayed(value))
    if value < Decimal("0.90"):
        return "red"
    if value <= Decimal("1.10"):
        return "white"
    if value <= Decimal("1.80"):
        return "green"
    return "gold"


def calculate(mode, kills, deaths, assists, rounds=None):
    if mode not in PARAMETERS:
        raise ValueError("Unsupported mode")
    inputs = {"kills": kills, "deaths": deaths, "assists": assists, "rounds": rounds}
    if any(v is None for v in (kills, deaths, assists)):
        return {"value": None, "tier": None, "inputs": inputs, "parameters": {}, "factors": {}}
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (kills, deaths, assists)):
        raise ValueError("Invalid statistics")
    tier = "rounds" if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0 else "match"
    norms = PARAMETERS[mode][tier]
    divisor = dec(rounds) if tier == "rounds" else Decimal(1)
    factors = {}
    for key in WEIGHTS:
        factor = dec(WEIGHTS[key]) * (dec(inputs[key]) / divisor / dec(norms[key]) - 1)
        factors[key] = -factor if key == "deaths" else factor
    value = max(Decimal(0), Decimal(1) + sum(factors.values()))
    return {
        "value": value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        "tier": tier,
        "inputs": inputs,
        "parameters": {"norms": norms, "weights": WEIGHTS, "mode": mode, "opponent_adjustment": False},
        "factors": {k: float(v) for k, v in factors.items()},
    }


def summarize(rows):
    count = len(rows)
    values = [dec(row.rating) for row in rows if row.rating is not None]
    tiers = {row.rating_tier for row in rows if row.rating is not None}
    versions = {getattr(row, "formula_version", VERSION) for row in rows if row.rating is not None}
    compatible = len(versions) <= 1
    average = sum(values) / len(values) if values and compatible else None

    def total(field):
        vals = [getattr(row, field) for row in rows]
        return sum(vals) if vals and all(v is not None for v in vals) else None

    kills, deaths, assists, headshots = (total(k) for k in ("kills", "deaths", "assists", "headshots"))
    return {
        "count": count,
        "rated_count": len(values),
        "rating": float(average) if average is not None else None,
        "rating_display": displayed(average),
        "rating_color": rating_color(average),
        "kd": kills / deaths if kills is not None and deaths else None,
        "kd_infinite": bool(deaths == 0 and kills is not None and kills > 0),
        "avg": kills / count if kills is not None and count else None,
        "assists": assists / count if assists is not None and count else None,
        "hs_percent": headshots / kills * 100 if headshots is not None and kills else None,
        "above_100": sum(v > 1 for v in values) / len(values) * 100 if values else None,
        "above_110": sum(v > Decimal("1.10") for v in values) / len(values) * 100 if values else None,
        "median": float(median(values)) if values else None,
        "stddev": float(pstdev(values)) if values else None,
        "tier": next(iter(tiers)) if len(tiers) == 1 else "mixed" if tiers else None,
        "formula_version": next(iter(versions)) if len(versions) == 1 else "mixed" if versions else VERSION,
        "formula_versions": sorted(versions),
    }
