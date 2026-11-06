from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman.economics import (AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling, profit_at,
                               scenarios_from_comps, sensitivities)
from cardman.fx import FxRate, FxTable, StaleRateError
from cardman.models import Money

T = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def test_fx_uses_rate_at_event_time_and_rejects_stale():
    fx = FxTable([FxRate("JPY", "TWD", D("0.21"), T - timedelta(days=30), "t"),
                  FxRate("JPY", "TWD", D("0.20"), T - timedelta(hours=2), "t", spread_pct=D("0.015"))])
    assert fx.convert(D(10000), "JPY", "TWD", T) == D("2000.00")
    assert fx.convert(D(10000), "JPY", "TWD", T, apply_spread=True) == D("2030.0000")
    with pytest.raises(StaleRateError):
        fx.convert(D(1), "JPY", "TWD", T - timedelta(days=10))


def test_no_silent_currency_coercion():
    with pytest.raises(ValueError):
        Money(D(1), "TWD") + Money(D(1), "JPY")


def inputs(**kw):
    base = dict(exit_gross=D(10000), acquisition=AcquisitionCosts(), exit=ExitCosts(), target_return=D("0.25"))
    base.update(kw)
    return CeilingInputs(**base)


def test_ceiling_is_exact_break_even_at_target_return():
    inp = inputs(acquisition=AcquisitionCosts(proxy_fee_pct=D("0.05"), proxy_fee_fixed=D(300),
                                              inbound_shipping=D(400), import_duty_pct=D("0.05")),
                 exit=ExitCosts(platform_fee_pct=D("0.1325"), outbound_shipping=D(150)))
    c = buy_ceiling(inp).buy_ceiling
    cost = inp.acquisition.all_in(c)
    assert profit_at(c, inp) >= D("0.25") * cost
    cost1 = inp.acquisition.all_in(c + 1)
    assert profit_at(c + 1, inp) < D("0.25") * cost1


def test_margin_turns_negative_gives_no_ceiling():
    inp = inputs(exit_gross=D(500), acquisition=AcquisitionCosts(inbound_shipping=D(600)))
    assert buy_ceiling(inp).buy_ceiling is None


def test_auction_premium_and_grading_lower_the_ceiling():
    plain = buy_ceiling(inputs()).buy_ceiling
    prem = buy_ceiling(inputs(acquisition=AcquisitionCosts(buyer_premium_pct=D("0.1")))).buy_ceiling
    graded = buy_ceiling(inputs(acquisition=AcquisitionCosts(grading=D(1500)))).buy_ceiling
    assert prem < plain and graded < plain


def test_time_to_sale_and_capital_cost_lower_the_ceiling():
    fast = buy_ceiling(inputs(capital_cost_monthly=D("0.01"))).buy_ceiling
    slow = buy_ceiling(inputs(capital_cost_monthly=D("0.01"), holding_months=D(6))).buy_ceiling
    assert slow < fast


def test_sparse_comps_abstain_or_widen():
    assert scenarios_from_comps([D(1000), D(1200)]).abstain_reason
    s = scenarios_from_comps([D(1000), D(1500), D(1200)])
    assert s.conservative == D(1000) and s.upside == D(1200)


def test_sensitivities_are_all_non_positive():
    sens = sensitivities(inputs(acquisition=AcquisitionCosts(inbound_shipping=D(300))))
    assert all(v is None or v <= 0 for k, v in sens.items() if k != "base")


def test_fx_spread_on_foreign_currency_shipping_lowers_the_ceiling_by_exactly_its_cost():
    base = inputs(acquisition=AcquisitionCosts(inbound_shipping=D(600), payment_fx_spread_pct=D("0.015")))
    spread = inputs(acquisition=AcquisitionCosts(inbound_shipping=D(600), payment_fx_spread_pct=D("0.015"),
                                                 shipping_fx_spread_pct=D("0.015")))
    assert spread.acquisition.all_in(D(1000)) - base.acquisition.all_in(D(1000)) == D("9.000")
    assert buy_ceiling(spread).buy_ceiling < buy_ceiling(base).buy_ceiling
    # default 0 keeps every earlier valuation identical (replay must still match)
    assert AcquisitionCosts(inbound_shipping=D(600)).linear() == (D(1), D(600))
