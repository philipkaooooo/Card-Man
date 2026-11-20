"""Checks on the decision math found in an audit: each test names the mistake it guards against."""
import json
from datetime import timedelta
from decimal import Decimal as D

from cardman.economics import AcquisitionCosts, ExitCosts
from cardman.opportunity import PriceBook
from tests.test_engine import LOB, LOB_1ST, NOW, SALES, VERIFIED, ask, drop, make, sold


def payload(e, key):
    return json.loads(e.db.execute("SELECT payload_json FROM opportunity WHERE key=?", (key,)).fetchone()[0])


def test_cheap_for_one_possible_print_is_not_called_underpriced(tmp_path):
    # LOB-EN001 could be the original (with sales) or the 25th-anniversary reprint (none): cheap only if original.
    e = make(tmp_path)
    drop(tmp_path, "s.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "a.jsonl", [ask("BUY", 400)])
    e.tick(NOW + timedelta(minutes=1))
    p = payload(e, "ebay:BUY")
    assert "UNDERPRICED" not in p["kinds"] and "RIGHT_PRINT_ONLY" in p["kinds"]
    e.confirm("ebay:BUY", VERIFIED, actor="t", now=NOW + timedelta(minutes=2))
    e.tick(NOW + timedelta(minutes=3))
    assert "UNDERPRICED" in payload(e, "ebay:BUY")["kinds"]


def test_live_asks_for_the_same_print_lengthen_the_wait_and_cost_capital(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "s.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "a.jsonl", [ask("A", 900, printing_id=LOB_1ST)])
    e.tick(NOW + timedelta(minutes=1))
    alone = next(s for s in payload(e, "ebay:A")["scenarios"] if s["printing_id"] == LOB_1ST)
    drop(tmp_path, "b.jsonl", [ask(f"Q{i}", 950, printing_id=LOB_1ST) for i in range(4)] + [ask("HIGH", 9000, printing_id=LOB_1ST)])
    e.tick(NOW + timedelta(minutes=2))
    queued = next(s for s in payload(e, "ebay:Q0")["scenarios"] if s["printing_id"] == LOB_1ST)
    # Four cheaper-or-equal copies ahead (A and three other Q's); the 9000 ask is above resale and does not compete.
    assert queued["months_to_sell"] > 4 * alone["months_to_sell"] * 0.99
    assert queued["max_buy"] < alone["max_buy"]


def test_holding_cost_is_in_scenario_profit_as_in_max_buy(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "s.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "a.jsonl", [ask("A", 900, printing_id=LOB_1ST)])
    e.tick(NOW + timedelta(minutes=1))
    p = payload(e, "ebay:A")
    s = next(s for s in p["scenarios"] if s["printing_id"] == LOB_1ST)
    _, ex = __import__("cardman.opportunity", fromlist=["costs_for"]).costs_for(e.profile, "INTL")
    before_carry = float(ex.net(D(str(round(s["resale_mid"], 2))))) - p["all_in_home"]
    assert s["profit_mid"] < before_carry


def test_plain_lot_is_shelved_but_a_collection_is_looked_at(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask("LOT", 50, title="yugioh lot 50 cards bulk"),
                               ask("BND", 50, title="retired binder collection 初期 yugioh")])
    e.tick(NOW)
    assert payload(e, "ebay:LOT")["status"] == "SHELVED"
    b = payload(e, "ebay:BND")
    assert b["status"] == "OPEN" and "COLLECTION" in b["kinds"]


def test_lot_card_is_not_priced_from_its_one_expensive_print(tmp_path):
    # "Blue-Eyes LOB-EN001" in a lot could be the reprint, which has no sales: the row stays unpriced.
    e = make(tmp_path)
    drop(tmp_path, "s.jsonl", [sold(i, 1000, 5 + i * 8, printing_id=LOB.id) for i in range(8)])
    e.tick(NOW)
    drop(tmp_path, "a.jsonl", [ask("L", 100, title="yugioh lot: Blue-Eyes White Dragon LOB-EN001, Dark Magician, bulk")])
    e.tick(NOW + timedelta(minutes=1))
    p = payload(e, "ebay:L")
    assert "LOT_UNDERPRICED" not in p["kinds"]
    assert all(r["value_low"] is None for r in p["lot"] if r["code"] == "LOB-EN001")


def test_exit_fixed_costs_are_per_card_when_a_lot_is_sold_card_by_card():
    ex = ExitCosts(platform_fee_pct=D("0.13"), platform_fee_fixed=D(10), outbound_shipping=D(100))
    cards = [D(500)] * 4
    assert sum(ex.net(c) for c in cards) == ex.net(sum(cards)) - 3 * (D(10) + D(100))


def test_fx_spread_also_applies_to_premium_and_proxy_fees():
    acq = AcquisitionCosts(buyer_premium_pct=D("0.10"), proxy_fee_pct=D("0.05"), proxy_fee_fixed=D(300), payment_fx_spread_pct=D("0.02"))
    a, b = acq.linear()
    assert a == D("1.10") + D("0.05") + D("0.02") * D("1.15")
    assert b == D(300) * D("1.02")
