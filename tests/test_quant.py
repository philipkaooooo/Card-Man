"""Verification, pricing, grading and private learning. All data here is made up to exercise behavior."""
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman import grading, pricing, verify
from cardman.economics import AcquisitionCosts, ExitCosts
from cardman.ledger import Ledger
from cardman.models import EvidenceType as ET
from tests.fixtures import obs

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
ago = lambda d: NOW - timedelta(days=d)
ACQ = AcquisitionCosts(inbound_shipping=D(100), payment_fx_spread_pct=D("0.015"))
EXIT = ExitCosts(platform_fee_pct=D("0.13"), outbound_shipping=D(100))


def sale(i, amount, days, **kw):
    return obs(i, kw.pop("et", ET.INDEPENDENT_TRANSACTION), str(amount), when=ago(days), capture_time=ago(days), **kw)


def sales_at(*prices, step=10):
    return [pricing.Sale(ago(i * step), float(p)) for i, p in enumerate(prices)]


def test_same_sale_on_two_sources_counts_once_and_keeps_the_stronger():
    a = sale(1, 1000, 10, source="auction_house", et=ET.INDEPENDENT_TRANSACTION)
    b = sale(2, 1005, 9, source="aggregator", et=ET.MARKETPLACE_MARKED_SOLD)
    v = verify.assess([a, b], NOW)
    assert verify.CROSS_POST_DUP in v["o2"].flags and v["o2"].excluded and not v["o1"].excluded
    kept, rejected = verify.filter_comps([a, b], [a, b], NOW)
    assert [o.id for o in kept] == ["o1"] and "verification" in rejected[0][1]


def test_outlier_is_flagged_not_silently_deleted():
    pool = [sale(i, p, i * 5) for i, p in enumerate((1000, 1010, 990, 1005, 995, 9000), 1)]
    v = verify.assess(pool, NOW)
    assert verify.OUTLIER_HIGH in v["o6"].flags and not v["o6"].excluded and v["o6"].trust < v["o1"].trust


def test_rising_sales_from_one_seller_look_like_painting():
    pool = [sale(i, p, 30 - i * 5, seller_ref="s1") for i, p in enumerate((800, 1000, 1300), 1)]
    assert all(verify.SELLER_CHURN in verify.assess(pool, NOW)[f"o{i}"].flags for i in (1, 2, 3))


def test_sale_then_relist_of_same_listing_is_excluded_as_possible_non_payment():
    s = sale(1, 1000, 20, seller_ref="s1", listing_id="L1")
    a = obs(2, ET.ACTIVE_ASKING, "1000", when=ago(10), capture_time=ago(10), seller_ref="s1", listing_id="L1")
    assert verify.RELISTED_AFTER_SALE in verify.assess([s, a], NOW)["o1"].flags


def test_verification_cannot_see_the_future():
    s = sale(1, 1000, 20, seller_ref="s1", listing_id="L1")
    a = obs(2, ET.ACTIVE_ASKING, "1000", when=ago(10), capture_time=ago(10), seller_ref="s1", listing_id="L1")
    assert verify.assess([s, a], ago(15))["o1"].flags == []


def test_shipping_gaming_and_slab_flips():
    assert verify.SHIPPING_GAMED in verify.assess([sale(1, 10, 5, shipping_amount=D(900))], NOW)["o1"].flags
    pool = [sale(1, 1000, 20, cert_number="123"), sale(2, 2000, 12, cert_number="123", source="x")]
    assert verify.CERT_FLIP in verify.assess(pool, NOW)["o2"].flags


def test_estimate_abstains_below_three_sales_and_is_deterministic():
    assert pricing.estimate(sales_at(1000, 1100), NOW) is None
    s = sales_at(1000, 1100, 1200, 900, 1050, 980, 1020)
    assert pricing.estimate(s, NOW) == pricing.estimate(s, NOW)


def test_recent_sales_outweigh_old_ones():
    recent_high = [pricing.Sale(ago(d), p) for d, p in ((5, 2000), (10, 2000), (15, 2000), (300, 1000), (310, 1000), (320, 1000), (330, 1000))]
    assert pricing.estimate(recent_high, NOW).mid == 2000


def test_falling_market_lowers_the_estimate_but_rising_never_raises_it():
    flat = pricing.estimate(sales_at(1000, 1000, 1000, 1000, 1000, 1000, 1000), NOW)
    falling = pricing.estimate(sales_at(1000, 1100, 1210, 1330, 1460, 1610, 1770, step=30), NOW)
    rising = pricing.estimate(sales_at(1770, 1610, 1460, 1330, 1210, 1100, 1000, step=30), NOW)
    raw_mid = 1330
    assert falling.trend_monthly < 0 and falling.mid < raw_mid
    assert rising.trend_monthly > 0 and rising.high <= 1770 and flat.trend_monthly == 0


def test_liquidity_and_time_to_sale():
    e = pricing.estimate(sales_at(1000, 1000, 1000, 1000, 1000, 1000, step=30), NOW)
    assert 0.9 < e.sales_per_month < 1.3
    assert pricing.months_to_sell(1.0, competing_listings=3) == 4.0 and pricing.months_to_sell(0) == float("inf")


def test_learned_bias_scales_every_number():
    s = sales_at(1000, 1100, 1200, 900, 1050, 980, 1020)
    a, b = pricing.estimate(s, NOW), pricing.estimate(s, NOW, bias=0.9)
    assert b.mid == pytest.approx(a.mid * 0.9) and b.low == pytest.approx(a.low * 0.9)


def test_risk_view_prices_a_good_and_a_bad_ask_differently():
    s = sales_at(1000, 1100, 1200, 900, 1050, 980, 1020, 1010, 1030, 990)
    e = pricing.estimate(s, NOW)
    kw = dict(target_return=D("0.2"), capital_cost_monthly=D("0.01"))
    good = pricing.risk_view(s, e, D(500), ACQ, EXIT, **kw)
    bad = pricing.risk_view(s, e, D(1100), ACQ, EXIT, **kw)
    assert good.expected_profit > 0 > bad.expected_profit and good.p_loss < 0.05 and bad.p_loss > 0.9
    assert good.risk_adjusted_profit <= good.expected_profit
    assert 500 < good.max_buy < 800


def test_slow_cards_are_charged_for_capital_lockup():
    fast = sales_at(1000, 1000, 1000, 1000, 1000, 1000, step=10)
    slow = sales_at(1000, 1000, 1000, 1000, 1000, 1000, step=30)
    kw = dict(target_return=D("0.2"), capital_cost_monthly=D("0.05"))
    a = pricing.risk_view(fast, pricing.estimate(fast, NOW), D(500), ACQ, EXIT, **kw)
    b = pricing.risk_view(slow, pricing.estimate(slow, NOW), D(500), ACQ, EXIT, **kw)
    assert b.months_held > a.months_held and b.max_buy < a.max_buy


def test_grading_refuses_without_own_results_or_a_prior():
    assert grading.evaluate(raw_all_in=1000, grading_cost=800, net_by_grade={"10": 8000}, own_results={}) is None


def test_grading_uses_own_results_and_finds_breakeven():
    r = grading.evaluate(raw_all_in=1000, grading_cost=800, net_by_grade={"10": 8000, "9": 2000, "8": 800},
                         own_results={"10": 2, "9": 6, "8": 2})
    assert r.n_own_results == 10 and r.expected_value == pytest.approx(0.2 * 8000 + 0.6 * 2000 + 0.2 * 800)
    assert r.p_loss == pytest.approx(0.2) and r.expected_profit > 0 and 0 < r.breakeven_top_probability < 1
    assert r.max_raw_buy == int((r.expected_value / 1.2) - 800)


def test_grades_without_a_price_count_as_zero():
    r = grading.evaluate(raw_all_in=1000, grading_cost=800, net_by_grade={"10": 8000}, own_results={"10": 1, "6": 1})
    assert r.expected_value == 4000


def test_ledger_is_validated_and_calibrates_from_real_outcomes(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    with pytest.raises(ValueError):
        L.add("PURCHASE", {"printing_id": "p"}, at=NOW)
    with pytest.raises(ValueError):
        L.add("SALE", {"purchase_id": "nope", "gross_home": 1, "net_home": 1}, at=NOW)
    assert L.calibration().resale_bias == 1.0
    for i in range(10):
        b = L.add("PURCHASE", {"printing_id": "p", "price_home": 500, "all_in_home": 600, "predicted_resale_home": 1000,
                               "predicted_all_in_home": 500, "route": "JP"}, at=ago(90))
        L.add("SALE", {"purchase_id": b, "gross_home": 800, "net_home": 700}, at=ago(30))
    c = L.calibration()
    assert c.n_sales == 10 and c.raw_ratio == pytest.approx(0.8) and 0.8 < c.resale_bias < 1
    assert c.resale_bias == pytest.approx((10 * 0.8 + 5) / 15)
    assert c.cost_overrun == pytest.approx(1.2) and c.median_months_to_sell == pytest.approx(2)
    assert c.win_rate == 1 and c.realized_profit == pytest.approx(1000) and c.capital_days > 0
    assert L.calibration(as_of=ago(60)).n_sales == 0                # no leakage
    assert L.calibration(route="TW").n_sales == 0


def test_lucky_outcomes_never_raise_estimates(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    b = L.add("PURCHASE", {"printing_id": "p", "price_home": 1, "all_in_home": 1, "predicted_resale_home": 100}, at=ago(9))
    L.add("SALE", {"purchase_id": b, "gross_home": 500, "net_home": 400}, at=ago(1))
    assert L.calibration().resale_bias == 1.0


def test_grade_counts_and_mistakes(tmp_path):
    L = Ledger(tmp_path / "l.sqlite")
    b = L.add("PURCHASE", {"printing_id": "p", "price_home": 1, "all_in_home": 1}, at=ago(9))
    L.add("GRADING_RESULT", {"purchase_id": b, "company": "PSA", "grade": "9", "cost_home": 700}, at=ago(5))
    L.add("MISTAKE", {"tag": "reprint_trap", "what": "bought the 25th"}, at=ago(1))
    assert L.grade_counts(printing_id="p") == {"9": 1} and L.grade_counts(printing_id="q") == {}
    assert L.calibration().mistakes == {"reprint_trap": 1}


def test_falling_trend_is_charged_for_the_time_it_takes_to_sell():
    from datetime import timedelta as td
    from cardman.pricing import Sale, estimate
    # Same 30%-a-month slide; one card sells weekly, the other monthly.
    fast = [Sale(NOW - td(days=d), 1000 * 0.7 ** (-d / 30)) for d in range(3, 60, 4)]
    slow = [Sale(NOW - td(days=d), 1000 * 0.7 ** (-d / 30)) for d in (5, 40, 75, 110, 145, 175)]
    f, s = estimate(fast, NOW), estimate(slow, NOW)
    lowest_fast = min(x.price for x in fast)
    assert f.low > 0.85 * lowest_fast * 0.9          # a fast seller is barely exposed to the slide
    assert (f.mid / statistics.median(x.price for x in fast)) > (s.mid / statistics.median(x.price for x in slow))
