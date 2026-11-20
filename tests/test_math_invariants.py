"""Checks that the numbers mean what they say. Each property holds for any reasonable input, so random cases are used."""
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman import pricing
from cardman.economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling, profit_at

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
EXIT = ExitCosts(platform_fee_pct=D("0.1325"), outbound_shipping=D(300), returns_reserve_pct=D("0.02"))


def acq(ship=400):
    return AcquisitionCosts(proxy_fee_pct=D("0.05"), payment_fx_spread_pct=D("0.02"), inbound_shipping=D(ship))


def sales(seed, n=12, center=20000):
    r = random.Random(seed)
    return [pricing.Sale(NOW - timedelta(days=r.uniform(1, 150)), center * r.uniform(0.75, 1.25)) for _ in range(n)]


def view(s, ask, **kw):
    e = pricing.estimate(s, NOW)
    args = dict(target_return=D("0.2"), capital_cost_monthly=D("0.01"), as_of=NOW)
    args.update(kw)
    a = args.pop("acquisition", acq())
    return pricing.risk_view(s, e, D(ask), a, EXIT, **args)


@pytest.mark.parametrize("seed", range(6))
def test_at_max_buy_the_chance_of_hitting_target_is_the_stated_confidence(seed):
    s = sales(seed)
    m = view(s, 1000).max_buy
    assert m is not None
    assert abs(view(s, m).p_target - 0.8) < 0.04


@pytest.mark.parametrize("seed", range(4))
def test_profit_falls_as_the_ask_rises(seed):
    s = sales(seed)
    profits = [view(s, a).expected_profit for a in (5000, 8000, 11000, 14000)]
    assert profits == sorted(profits, reverse=True)


def test_max_buy_falls_with_costs_target_and_competition():
    s = sales(3)
    base = view(s, 1000).max_buy
    assert view(s, 1000, acquisition=acq(ship=1500)).max_buy < base
    assert view(s, 1000, target_return=D("0.4")).max_buy < base
    assert view(s, 1000, competing_listings=8).max_buy <= base
    assert view(s, 1000, confidence=0.95).max_buy <= base


def test_extra_uncertainty_does_not_raise_expected_resale():
    import statistics
    s = sales(9, n=4)          # few sales: wide interval, so a large spread multiplier
    e = pricing.estimate(s, NOW)
    assert (e.ci_high - e.ci_low) / e.mid > 0.1
    v = view(s, 1000, as_of=None)                     # equal weights, so the expectation is easy to state
    carry = float(acq().all_in(D(1000))) * 0.01 * v.months_held
    implied_mean_net = v.expected_profit + float(acq().all_in(D(1000))) + carry
    scale = e.mid / statistics.median(x.price for x in s)
    plain = statistics.fmean(float(EXIT.net(D(str(round(x.price * scale, 2))))) for x in s)
    assert abs(implied_mean_net / plain - 1) < 0.03


def test_ceiling_is_exactly_the_break_point_of_the_target():
    for exit_gross in (D(5000), D(20000), D(90000)):
        inp = CeilingInputs(exit_gross, acq(), EXIT, D("0.2"), D(2), D("0.01"))
        c = buy_ceiling(inp).buy_ceiling
        need = lambda b: profit_at(b, inp) - (D("0.2") + D("0.02")) * inp.acquisition.all_in(b)
        assert need(c) >= 0 > need(c + 2)


def test_slow_sellers_and_crowded_markets_wait_longer():
    assert pricing.months_to_sell(2.0, 4) > pricing.months_to_sell(2.0, 0) > pricing.months_to_sell(4.0, 0)


def test_annualized_return_is_not_inflated_by_very_fast_sales():
    s = [pricing.Sale(NOW - timedelta(days=d), 20000) for d in range(1, 30)]   # ~30 sales a month
    v = view(s, 10000)
    simple = v.expected_profit / float(acq().all_in(D(10000)))
    assert v.annualized_return <= (1 + simple) ** 12


# -- decisions on listings: built on the engine test helpers (real catalog subset, made-up market rows) --------------
import json as _json

from cardman.intel import analyze
from tests.test_engine import CAT, NOW as ENGINE_NOW, SALES, ask, drop, make


def _opp(e, key):
    return _json.loads(e.db.execute("SELECT payload_json FROM opportunity WHERE key=?", (key,)).fetchone()[0])


def _ref(url):
    return {"set_price": "253.34", "set_rarity": "Ultra Rare", "name": "Blue-Eyes White Dragon"}


def test_a_guide_price_is_a_capped_lead_never_an_expected_profit(tmp_path):
    e = make(tmp_path, reference=_ref)
    drop(tmp_path, "a.jsonl", [ask("R", 20)])
    e.tick(ENGINE_NOW)
    p = _opp(e, "ebay:R")
    assert "REFERENCE_GAP" in p["kinds"] and p["expected_profit"] is None and p["best_profit"] is None
    assert p["priority"] <= 2 * 60 * 10 * 0.25 + 1          # capped at two quick looks at 600/h


def test_slabs_and_japanese_listings_never_use_an_ungraded_english_guide(tmp_path):
    e = make(tmp_path, reference=_ref)
    drop(tmp_path, "a.jsonl", [ask("G", 20, title="PSA 10 Blue-Eyes White Dragon LOB-EN001 Ultra Rare"),
                               ask("J", 20, title="Blue-Eyes White Dragon LOB-EN001 Ultra Rare JAPAN")])
    e.tick(ENGINE_NOW)
    for k in ("ebay:G", "ebay:J"):
        p = _opp(e, k)
        assert "REFERENCE_GAP" not in p["kinds"] and not any(s["basis"] == "reference" for s in p["scenarios"])


def test_titles_naming_several_cards_are_lots_and_single_names_are_not():
    assert analyze("Blue-Eyes White Dragon & Dark Magician LOB", CAT).parsed.is_lot
    assert analyze("Blue-Eyes White Dragon + Dark Magician", CAT).parsed.is_lot
    assert not analyze("Blue-Eyes White Dragon LOB-EN001 Ultra Rare", CAT).parsed.is_lot
    assert not analyze("Dark Magician Girl & Kuriboh sleeves", CAT).parsed.is_lot or len(CAT.family_matches("Dark Magician Girl & Kuriboh sleeves")[0]) >= 2
    assert not analyze("Dark Magician Girl LOB-EN005", CAT).parsed.is_lot      # one name containing another is one card


def test_a_small_priced_share_does_not_speak_for_the_listing(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "s.jsonl", SALES)          # sales exist only for one edition of one of the prints
    e.tick(ENGINE_NOW)
    drop(tmp_path, "a.jsonl", [ask("A", 400, title="Blue-Eyes White Dragon LOB-EN001 Ultra Rare")])   # no edition claim
    e.tick(ENGINE_NOW)
    p = _opp(e, "ebay:A")
    assert p["priced_share"] < 0.5 and p["expected_profit"] is None
    assert p["best_profit"] > 0 and p["status"] == "OPEN" and p["priority"] > 0


def test_lots_rank_by_what_they_name_not_one_flat_score(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask("L1", 300, title="Vintage Yu-Gi-Oh binder collection lot"),
                               ask("L2", 300, title="Vintage Yu-Gi-Oh binder collection lot Blue-Eyes White Dragon, Dark Magician"),
                               ask("L3", 300, title="Vintage Yu-Gi-Oh binder collection lot 詳細不明")])
    e.tick(ENGINE_NOW)
    pr = {k: _opp(e, f"ebay:{k}")["priority"] for k in ("L1", "L2", "L3")}
    assert pr["L2"] > pr["L1"] and pr["L3"] > pr["L1"]


def test_max_buy_does_not_move_with_the_ask():
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal as D
    from cardman import pricing
    from cardman.economics import AcquisitionCosts, ExitCosts
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    sales = [pricing.Sale(now - timedelta(days=5 + 9 * i), 1000 + 37 * (i % 5)) for i in range(10)]
    est = pricing.estimate(sales, now)
    acq, ex = AcquisitionCosts(inbound_shipping=D(60)), ExitCosts(platform_fee_pct=D("0.13"), outbound_shipping=D(60))
    caps = {pricing.risk_view(sales, est, D(ask), acq, ex, target_return=D("0.2"), capital_cost_monthly=D("0.01"), as_of=now).max_buy
            for ask in (300, 500, 700, 900)}
    assert len(caps) == 1
