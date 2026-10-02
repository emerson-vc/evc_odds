import pytest

from src.pricing.devig import ProportionalDevig, get_method, overround
from src.pricing.ev import ev_percent, expected_value
from src.pricing.odds import (
    american_implied_probability,
    american_to_decimal,
    decimal_to_american,
    implied_probability,
    probability_to_american,
    probability_to_decimal,
)


@pytest.mark.parametrize(
    "american, decimal",
    [(100, 2.0), (125, 2.25), (154, 2.54), (-110, 1 + 100 / 110), (-184, 1 + 100 / 184), (-100, 2.0)],
)
def test_american_decimal_roundtrip(american, decimal):
    assert american_to_decimal(american) == pytest.approx(decimal)
    assert decimal_to_american(decimal) == (100 if american == -100 else american)


def test_claude_md_implied_probability_formulas():
    # +A: 100 / (A + 100);  -A: A / (A + 100)
    assert american_implied_probability(125) == pytest.approx(100 / 225)
    assert american_implied_probability(-110) == pytest.approx(110 / 210)
    assert implied_probability(2.5) == pytest.approx(0.4)


@pytest.mark.parametrize("bad", [0, 50, -99.9])
def test_invalid_american_rejected(bad):
    with pytest.raises(ValueError):
        american_to_decimal(bad)


def test_probability_to_fair_odds():
    assert probability_to_decimal(0.5) == pytest.approx(2.0)
    assert probability_to_american(0.5238095) == -110
    assert probability_to_american(0.4) == 150
    with pytest.raises(ValueError):
        probability_to_decimal(1.0)


def test_proportional_devig_symmetric_market():
    d = american_to_decimal(-110)
    fair = ProportionalDevig().fair_probabilities({"over": d, "under": d})
    assert fair == {"over": pytest.approx(0.5), "under": pytest.approx(0.5)}
    assert overround({"over": d, "under": d}) == pytest.approx(2 * 110 / 210 - 1)


def test_proportional_devig_fanduel_moneyline():
    # Observed FanDuel IND -184 / WAS +154 (2026-10-01 capture).
    fair = get_method("proportional").fair_probabilities(
        {"IND": american_to_decimal(-184), "WAS": american_to_decimal(154)}
    )
    raw_ind, raw_was = 184 / 284, 100 / 254
    assert fair["IND"] == pytest.approx(raw_ind / (raw_ind + raw_was))
    assert sum(fair.values()) == pytest.approx(1.0)


def test_devig_refuses_one_sided_quote():
    with pytest.raises(ValueError):
        ProportionalDevig().fair_probabilities({"over": 1.9})


def test_unknown_devig_method():
    with pytest.raises(ValueError):
        get_method("power")


def test_ev_claude_md_example():
    # CLAUDE.md §37: fair 52.4%, BetMGM +125 -> EV +17.9%
    assert ev_percent(0.524, american_to_decimal(125)) == pytest.approx(17.9, abs=0.05)


def test_ev_zero_at_fair_price_and_negative_below():
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)
    assert expected_value(0.5, american_to_decimal(-110)) < 0
