"""The plain call and every number behind it, checked against the cost model it claims to use."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman.advice import advise
from cardman.economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling

T = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
ACQ = {"payment_fx_spread_pct": D("0.015"), "inbound_shipping": D("600"), "proxy_fee_pct": D("0"),
       "import_duty_pct": D("0")}
EXIT = {"platform_fee_pct": D("0.1325"), "outbound_shipping": D("300"), "returns_reserve_pct": D("0.02")}
CI = {"acquisition": ACQ, "exit": EXIT, "target_return": D("0.20"), "holding_months": D("2"),
      "capital_cost_monthly": D("0.01")}
SALES = [D("28800"), D("26240"), D("30400")]
GATES_OK = [{"name": n, "passed": True, "detail": "ok"} for n in
            ("identity", "authenticity", "comps", "ceiling", "fresh_availability")]
TODO = {"authenticity": "check photos", "comps": "add sales", "identity": "pin printing",
        "fresh_availability": "recheck"}


def comps(prices=SALES, synthetic=False, days=(34, 15, 0)):
    return [{"date": T - timedelta(days=d), "home": p, "type": "MARKETPLACE_MARKED_SOLD", "source": "ebay",
             "synthetic": synthetic} for p, d in zip(prices, days)]


def ceiling():
    return buy_ceiling(CeilingInputs(min(SALES), AcquisitionCosts(**ACQ), ExitCosts(**EXIT), D("0.20"), D("2"),
                                     D("0.01"))).buy_ceiling


def run(ask, gates=GATES_OK, **kw):
    scen = {"conservative": min(SALES), "base": D("28800"), "upside": D("28800")}
    return advise(ci=CI, ask=ask, ceiling=kw.pop("ceiling", ceiling()), scen=scen, comps=kw.pop("comps", comps()),
                  valued_at=T, gates=gates, identity_status=kw.pop("identity", "RESOLVED"),
                  money=lambda x, rnd="near": f"{x:.2f}", gate_todo=TODO)


def test_thresholds_are_exact():
    acq, ex = AcquisitionCosts(**ACQ), ExitCosts(**EXIT)
    a = run(D("16000"))
    assert a.pay_up_to == D("17125")
    # walk-away: profit >= 0 at the low-end resale, and one unit more loses money
    assert ex.net(min(SALES)) - acq.all_in(a.walk_away) >= 0
    assert ex.net(min(SALES)) - acq.all_in(a.walk_away + 1) < 0
    # break-even resale: net of it equals the all-in cost at the ask
    assert abs(ex.net(a.breakeven_resale) - acq.all_in(D("16000"))) < D("0.0001")
    assert a.all_in_at_ask == D("16840.000")
    cons = a.at_ask[0]
    assert cons.profit == ex.net(min(SALES)) - D("16840") and round(cons.ret, 3) == D("0.303")
    assert a.required == D("0.22")


def test_calls_follow_the_price_zones():
    assert run(D("16000")).call == "BUY"
    assert run(D("19000")).call == "OFFER"            # between pay-up-to (17,125) and walk-away (21,023)
    assert run(D("22000")).call == "SKIP"
    pending = [dict(g, passed=(g["name"] != "authenticity")) for g in GATES_OK]
    a = run(D("16000"), gates=pending)
    assert a.call == "CHECK" and a.todo == ["check photos"]
    assert run(D("16000"), identity="CANDIDATES").call == "HOLD"
    assert run(D("16000"), ceiling=None, comps=comps()[:2]).call == "HOLD"


def test_win_rate_counts_each_recent_sale():
    a = run(D("16000"))
    assert (a.wins, a.hits, len(a.per_sale)) == (3, 3, 3)
    a = run(D("20000"))                               # profitable against some sales only
    assert a.wins == 3 and a.hits == 0
    a = run(D("21500"))
    assert 0 < a.wins < 3


def test_downside_and_cushion():
    a = run(D("16000"))
    assert [o.resale for o in a.downside] == [min(SALES) * D("0.9"), min(SALES) * D("0.8"), min(SALES) * D("0.7")]
    assert a.downside[0].profit > 0 > a.downside[2].profit
    assert D("0.22") < a.cushion_pct < D("0.24")


def test_confidence_and_flags_are_honest():
    a = run(D("16000"), comps=comps(synthetic=True))
    assert a.confidence == "Low" and any("synthetic" in w for w in a.confidence_why)
    assert a.flags[0][0] == "high" and "Low confidence" in a.flags[0][1]
    wide = run(D("16000"), comps=comps(prices=[D("10000"), D("26240"), D("40000")]))
    assert any("spread out" in t for _, t in wide.flags)
    stale = run(D("16000"), comps=comps(days=(200, 150, 120)))
    assert stale.confidence == "Low" and any("days old" in t for _, t in stale.flags)


def test_math_steps_end_at_the_ceiling():
    a = run(D("16000"))
    assert a.steps[-1][0] == "= pay up to" and a.steps[-1][2] == "17125.00"
