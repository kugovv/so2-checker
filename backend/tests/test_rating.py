from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.rating import VERSION, calculate, displayed, rating_color, summarize


def row(rating="1.00", kills=15, deaths=15, assists=4, headshots=5, tier="rounds", version=VERSION):
    return SimpleNamespace(rating=Decimal(rating) if rating is not None else None, kills=kills, deaths=deaths,
                           assists=assists, headshots=headshots, rating_tier=tier, formula_version=version)


def test_exact_mean_112():
    result = summarize([row("1.10") for _ in range(4)] + [row("1.20")])
    assert result["rating_display"] == "1.12"
    assert result["rating"] == 1.12
    assert result["count"] == 5


def test_one_good_match_equal_weight():
    assert summarize([row() for _ in range(19)] + [row("2.00")])["rating_display"] == "1.05"


def test_small_sample_not_inflated_to_twenty():
    result = summarize([row("1.20") for _ in range(7)])
    assert result["count"] == result["rated_count"] == 7
    assert result["rating_display"] == "1.20"


def test_no_matches_and_zero_deaths():
    empty = summarize([])
    assert empty["rating"] is None and empty["avg"] is None and empty["kd"] is None
    zero = summarize([row(kills=8, deaths=0)])
    assert zero["kd"] is None and zero["kd_infinite"] is True
    all_zero = summarize([row(kills=0, deaths=0, headshots=0)])
    assert all_zero["kd"] is None and all_zero["hs_percent"] is None and not all_zero["kd_infinite"]


def test_aggregate_kd_avg_weighted_hs():
    result = summarize([row(kills=10, deaths=2, assists=2, headshots=5), row(kills=20, deaths=10, assists=8, headshots=2)])
    assert result["kd"] == 2.5  # 30/12, not mean(5, 2)
    assert result["avg"] == 15
    assert result["assists"] == 5
    assert result["hs_percent"] == pytest.approx(7 / 30 * 100)


@pytest.mark.parametrize("value,color,display", [
    ("0.894", "red", "0.89"), ("0.895", "white", "0.90"), ("0.90", "white", "0.90"),
    ("1.104", "white", "1.10"), ("1.105", "green", "1.11"), ("1.10", "white", "1.10"),
    ("1.804", "green", "1.80"), ("1.805", "gold", "1.81"), ("1.80", "green", "1.80")])
def test_colors_follow_display(value, color, display):
    assert displayed(value) == display
    assert rating_color(value) == color
    assert summarize([row(value)])["rating_color"] == color


def test_assists_positive_and_round_normalization():
    assert calculate("competitive", 9, 10, 10, 20)["value"] > calculate("competitive", 9, 10, 0, 20)["value"]
    assert calculate("competitive", 10, 10, 4, 10)["value"] == calculate("competitive", 20, 20, 8, 20)["value"]


def test_modes_have_separate_norms_and_fallback():
    assert calculate("competitive", 10, 10, 4, 10)["value"] != calculate("allies", 10, 10, 4, 10)["value"]
    assert calculate("competitive", 10, 10, 4)["tier"] == "match"
    assert calculate("competitive", 10, 10, None)["value"] is None
    assert calculate("competitive", 10, 10, 4, 0)["tier"] == "match"
    with pytest.raises(ValueError):
        calculate("duel", 10, 10, 4, 10)


def test_missing_fields_never_become_zero():
    result = summarize([row(), row(kills=None, headshots=None)])
    assert result["avg"] is None and result["kd"] is None and result["hs_percent"] is None
    assert result["assists"] == 4


def test_stability_does_not_change_main_average():
    stable = summarize([row("1.10"), row("1.10")])
    volatile = summarize([row("0.10"), row("2.10")])
    assert stable["rating"] == volatile["rating"]
    assert stable["stddev"] != volatile["stddev"]


def test_mixed_formula_versions_not_averaged():
    result = summarize([row(version="old"), row()])
    assert result["rating"] is None and result["formula_version"] == "mixed"
