from decimal import Decimal

from app.rating import calculate, displayed, rating_color


def test_harny_actual_screenshot_fallback_and_confirmed_rounds():
    actual = calculate('competitive', 22, 7, 0, rounds=None)
    assert actual['tier'] == 'match'
    assert actual['inputs']['rounds'] is None
    assert actual['value'] == Decimal('1.33666667')
    assert displayed(actual['value']) == '1.34'
    assert calculate('competitive', 22, 7, 0, 14)['value'] == Decimal('1.78061224')
    assert calculate('competitive', 22, 7, 0, 28)['value'] == Decimal('1.14030612')
    assert actual['parameters']['opponent_adjustment'] is False


def test_gold_possible_with_deaths_and_no_low_ceiling():
    for mode in ('competitive', 'allies'):
        for rounds in (None, 14):
            value = calculate(mode, 35, 10, 2, rounds)['value']
            assert rating_color(value) == 'gold'
            assert calculate(mode, 45, 10, 2, rounds)['value'] > value


def test_lower_bound_and_missing_assists_not_zero():
    assert calculate('competitive', 0, 100, 0)['value'] == 0
    assert calculate('competitive', 22, 7, None)['value'] is None
    assert calculate('competitive', 22, 7, 0)['value'] is not None
