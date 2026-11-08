"""The loop end to end on real catalog data with made-up market rows: nothing here is market evidence."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman.validation import run_backtest
from cardman.engine import Engine, load_profile
from cardman.identity import Catalog
from cardman.market_sources import EbayAdapter, RawListing, from_record, parse_bytes, parse_saved_html
from cardman.models import EvidenceType as ET
from cardman.evidence import RawEvidenceStore
from tests.real_catalog import real_catalog

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
RC = real_catalog()
CAT = Catalog(list(RC.families.values()), RC.printings, edition_rules={"Legend of Blue Eyes White Dragon": ["1st", "Unlimited"]})
LOB = next(p for p in CAT.by_code["LOB-EN001"] if p.product_name == "Legend of Blue Eyes White Dragon")
LOB_1ST = f"{LOB.id}|ed:1st"
TITLE = "Blue-Eyes White Dragon LOB-EN001 1st Edition Ultra Rare"
VERIFIED = {"printed_set_code": "LOB-EN001", "verified_edition": "1st", "verified_product": "Legend of Blue Eyes White Dragon"}


def fx_get(url):
    if "er-api" in url:
        return {"result": "success", "time_last_update_unix": int(NOW.timestamp()), "rates": {"TWD": 32, "JPY": 150}}
    return {"usd": {"twd": 32.0}, "jpy": {"twd": 0.2}}


def make(tmp_path, reference=None, profile=None):
    return Engine(str(tmp_path / "s.sqlite"), catalog=CAT, profile=profile or load_profile(), inbox=tmp_path / "inbox", raw_dir=tmp_path / "raw",
                  fx_get=fx_get, reference_get=reference, use_reference=bool(reference))


def drop(tmp_path, name, rows):
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / name).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def sold(i, usd, days_ago, **kw):
    return {"source": kw.pop("source", "ebay"), "listing_id": f"s{i}", "title": TITLE, "price": str(usd), "currency": "USD", "kind": "transaction",
            "url": f"https://example.test/{i}", "event_time": (NOW - timedelta(days=days_ago)).isoformat(), "printing_id": LOB_1ST, **kw}


def ask(lid, usd, **kw):
    return {"source": kw.pop("source", "ebay"), "listing_id": lid, "title": TITLE, "price": str(usd), "currency": "USD", "url": f"https://example.test/{lid}", **kw}


SALES = [sold(i, 1000 + i * 10, 5 + i * 8) for i in range(8)]


def test_ebay_stays_off_quietly_without_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    a = EbayAdapter(RawEvidenceStore(tmp_path), ["blue eyes"])
    assert not a.status().on and a.poll(NOW) == ([], [])
    rep = make(tmp_path).tick(NOW)
    assert next(r for r in rep.routes if r["name"] == "ebay")["on"] is False and not rep.errors


def test_inbox_formats_and_bad_rows_are_named():
    got, errs = parse_bytes("mercari_jp.2026.csv", b"title,price,currency\nBlue-Eyes,1000,JPY\n,5,JPY\n")
    assert got[0].source == "mercari_jp" and got[0].market == "JP" and got[0].kind == "ask" and "row 2" in errs[0]
    assert parse_bytes("a.html", b"<html></html>")[1] == ["a.html: no schema.org Product data in this page; export a CSV instead"]


def test_saved_html_reads_structured_product_data():
    html = '<script type="application/ld+json">{"@type":"Product","name":"Blue-Eyes LOB-EN001","offers":{"price":"1200","priceCurrency":"JPY","availability":"https://schema.org/SoldOut"}}</script>'
    got = parse_saved_html(html, source="shop")
    assert got[0].kind == "sold" and got[0].price == D(1200) and got[0].currency == "JPY"


def test_same_file_is_read_once_and_unchanged_listings_are_not_reanalyzed(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask("A", 400)])
    r1 = e.tick(NOW)
    assert r1.new_listings == 1 and r1.analyzed == 1
    r2 = e.tick(NOW + timedelta(minutes=15))
    assert r2.new_listings == 0 and r2.analyzed == 0 and r2.skipped_unchanged == 1


def test_price_changes_are_kept_as_history(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask("A", 400)])
    e.tick(NOW)
    drop(tmp_path, "b.jsonl", [ask("A", 300)])
    r = e.tick(NOW + timedelta(hours=1))
    assert r.price_changes == 1 and r.analyzed == 1
    kinds = [x[0] for x in e.db.execute("SELECT kind FROM listing_event WHERE key='ebay:A' ORDER BY at")]
    assert kinds == ["FIRST_SEEN", "PRICE_CHANGE"]


def test_full_path_observe_spectate_then_person_confirms_then_workflow_takes_over(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "ask.jsonl", [ask("BUY", 400)])
    e.tick(NOW + timedelta(minutes=1))
    row = e.db.execute("SELECT stage, status, snapshot_id FROM opportunity WHERE key='ebay:BUY'").fetchone()
    # Seller's title alone is never enough: watched, not escalated.
    assert row == ("SPECTATE", "OPEN", None)
    e.confirm("ebay:BUY", VERIFIED, actor="test", now=NOW + timedelta(minutes=2))
    e.tick(NOW + timedelta(minutes=3))
    row = e.db.execute("SELECT stage, status, snapshot_id, printing_id FROM opportunity WHERE key='ebay:BUY'").fetchone()
    assert row[1] == "ESCALATED" and row[2] and row[3] == LOB_1ST
    assert row[0] == "SPECTATE"          # authenticity not reviewed: the original gate still holds
    e.confirm("ebay:BUY", {"authenticity_ok": True}, actor="test", now=NOW + timedelta(minutes=4))
    e.tick(NOW + timedelta(minutes=5))
    row = e.db.execute("SELECT stage, snapshot_id FROM opportunity WHERE key='ebay:BUY'").fetchone()
    assert row[0] == "QUALIFIED"
    assert e.store.snapshot_meta(row[1])["stage"] == "QUALIFIED"       # same store the YES / WAIT / NO page reads


def test_overpriced_listing_is_not_escalated_even_when_confirmed(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES + [ask("DEAR", 5000)])
    e.tick(NOW)
    e.confirm("ebay:DEAR", VERIFIED, actor="test", now=NOW)
    e.tick(NOW + timedelta(minutes=1))
    row = e.db.execute("SELECT stage, status, snapshot_id FROM opportunity WHERE key='ebay:DEAR'").fetchone()
    assert row[2] is None and row[1] != "ESCALATED"


def test_counterfeit_is_rejected_with_reason(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask("F", 10, title="Blue-Eyes White Dragon LOB-EN001 proxy")])
    r = e.tick(NOW)
    assert r.rejected == 1
    p = json.loads(e.db.execute("SELECT payload_json FROM opportunity WHERE key='ebay:F'").fetchone()[0])
    assert p["status"] == "REJECTED" and "proxy" in p["reasons"][0]


def test_value_of_information_ranks_real_uncertainty_above_a_settled_loser(tmp_path):
    e = make(tmp_path)
    # Sales only for the original printing: the reprint sharing the code has none.
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "asks.jsonl", [ask("CHEAP", 300), ask("DEAR", 5000)])
    e.tick(NOW + timedelta(minutes=1))
    pri = dict(e.db.execute("SELECT key, priority FROM opportunity"))
    assert pri["ebay:CHEAP"] >= pri["ebay:DEAR"]


def test_reference_price_is_only_ever_a_lead(tmp_path):
    calls = []

    def ref(url):
        calls.append(url)
        return {"set_price": "253.34", "set_rarity": "Ultra Rare", "name": "Blue-Eyes White Dragon"}
    e = make(tmp_path, reference=ref)
    drop(tmp_path, "a.jsonl", [ask("R", 20)])
    e.tick(NOW)
    p = json.loads(e.db.execute("SELECT payload_json FROM opportunity WHERE key='ebay:R'").fetchone()[0])
    assert "REFERENCE_GAP" in p["kinds"] and "UNDERPRICED" not in p["kinds"] and p["stage"] == "SPECTATE"
    assert any(s["basis"] == "reference" for s in p["scenarios"])
    assert e.store.db.execute("SELECT COUNT(*) FROM observation WHERE evidence_type='AGGREGATE_ESTIMATE'").fetchone()[0] == 0
    assert len(calls) == len(set(calls))    # one lookup per code per day


def test_sold_rows_without_identification_do_not_become_comparables(tmp_path):
    e = make(tmp_path)
    row = sold(1, 9999, 3)
    row.pop("printing_id")
    drop(tmp_path, "a.jsonl", [row, ask("A", 400)])
    e.tick(NOW)
    val = e.store.observations_for(LOB.id, as_of=NOW)
    assert val and all(o.printing_candidates != (LOB_1ST,) for o in val)


def test_unknown_printing_id_is_refused_and_named(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [sold(1, 100, 3, printing_id="p:not-real")])
    assert any("unknown printing_id" in x for x in e.tick(NOW).errors)


def test_regional_gap_and_price_shift_signals(tmp_path):
    e = make(tmp_path)
    jp = [sold(100 + i, 400 + i, 20 + i, source="yahoo_auctions_jp") for i in range(4)]
    intl = [sold(200 + i, 1500 + i, 20 + i, source="ebay") for i in range(4)]
    recent = [sold(300 + i, 2500, 2 + i, source="ebay") for i in range(3)]
    drop(tmp_path, "a.jsonl", jp + intl + recent)
    r = e.tick(NOW)
    kinds = {k for (k,) in e.db.execute("SELECT kind FROM market_signal")}
    assert "REGIONAL_JP_TO_INTL" in kinds and "PRICE_SHIFT" in kinds and r.signals >= 2
    e.tick(NOW + timedelta(minutes=1))
    assert e.db.execute("SELECT COUNT(*) FROM market_signal").fetchone()[0] == r.signals   # not re-alerted the same day


def test_seller_dumping_stock_is_noticed(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "a.jsonl", [ask(f"D{i}", 400, seller="estate1") for i in range(6)])
    e.tick(NOW)
    assert e.db.execute("SELECT COUNT(*) FROM market_signal WHERE kind='SELLER_ACTIVITY'").fetchone()[0] == 1


def test_learned_bias_lowers_the_next_valuation(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    b = e.ledger.add("PURCHASE", {"printing_id": LOB_1ST, "price_home": 100, "all_in_home": 100, "predicted_resale_home": 1000}, at=NOW)
    for _ in range(10):
        e.ledger.add("SALE", {"purchase_id": b, "gross_home": 500, "net_home": 450}, at=NOW)
    assert e.ledger.calibration(NOW).resale_bias < 1


def test_backtest_refuses_a_verdict_without_real_outcomes_and_charges_for_speed(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    t1 = NOW + timedelta(minutes=1)
    drop(tmp_path, "ask.jsonl", [ask("A", 300)])
    e.tick(t1)
    e.confirm("ebay:A", VERIFIED, actor="test", now=t1)
    e.tick(t1 + timedelta(minutes=1))
    # A later sale exists, and the listing vanished 2 hours after it appeared.
    drop(tmp_path, "later.jsonl", [sold(50, 1000, -10)])
    e.db.execute("UPDATE listing SET ended_at=? WHERE key='ebay:A'", ((t1 + timedelta(hours=2)).isoformat(),))
    e.db.commit()
    e.tick(NOW + timedelta(days=30))
    res = run_backtest(e, NOW + timedelta(days=30))
    assert not res["has_verdict"] and "Not enough real outcomes" in res["verdict"]
    assert res["strategies"]["engine"]["bought"] == 1 and res["strategies"]["engine"]["counterfactual_outcomes"] == 1
    assert res["strategies"]["exact_tracker"]["missed_gone"] == 1   # 6-hour delay, gone after 2
    assert res["strategies"]["name_tracker"]["missed_gone"] == 1


def test_real_outcome_beats_counterfactual_and_verdict_appears_at_threshold(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    t1 = NOW + timedelta(minutes=1)
    rows = [ask(f"A{i}", 300) for i in range(20)]
    drop(tmp_path, "ask.jsonl", rows)
    e.tick(t1)
    for r in rows:
        k = f"ebay:{r['listing_id']}"
        e.confirm(k, VERIFIED, actor="test", now=t1)
    e.tick(t1 + timedelta(minutes=1))
    for r in rows:
        k = f"ebay:{r['listing_id']}"
        b = e.ledger.add("PURCHASE", {"printing_id": LOB_1ST, "price_home": 9600, "all_in_home": 10000}, at=t1, listing_key=k)
        e.ledger.add("SALE", {"purchase_id": b, "gross_home": 20000, "net_home": 17000}, at=t1 + timedelta(days=30))
    res = run_backtest(e, NOW + timedelta(days=60))
    assert res["has_verdict"] and res["strategies"]["engine"]["real_outcomes"] == 20
    assert res["strategies"]["engine"]["net_profit"] == pytest.approx(140000)


def test_opportunity_pages_render_the_engine_queue(tmp_path):
    from cardman.store import Store
    from cardman.ui import view_opportunities, view_opportunity
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    drop(tmp_path, "ask.jsonl", [ask("BUY", 400)])
    e.tick(NOW + timedelta(minutes=1))
    st = Store(str(tmp_path / "s.sqlite"))
    h = view_opportunities(st, NOW + timedelta(minutes=2), "tok", "USD", "open")
    assert "ebay:BUY" in h and "Last check" in h and "Expected profit" in h
    d = view_opportunity(st, "ebay:BUY", NOW + timedelta(minutes=2), "tok", "USD")
    assert "Every possibility" in d and "Confirm from the card itself" in d and "LOB-EN001" in d
    assert "$" in d                     # engine TWD amounts shown in USD at the stored rate


def test_reference_prices_are_reused_for_a_week_and_failures_for_a_day(tmp_path):
    import sqlite3
    from cardman.market_sources import YgoprodeckReference
    calls = []

    def ok(url):
        calls.append(url)
        return {"set_price": "10", "set_rarity": "Ultra Rare"}
    r = YgoprodeckReference(sqlite3.connect(":memory:"), get=ok)
    r.price("LOB-EN001", NOW)
    r.price("LOB-EN001", NOW + timedelta(days=6))
    assert len(calls) == 1
    r.price("LOB-EN001", NOW + timedelta(days=8))
    assert len(calls) == 2

    def down(url):
        calls.append(url)
        raise OSError("offline")
    r2 = YgoprodeckReference(sqlite3.connect(":memory:"), get=down)
    assert r2.price("X-EN001", NOW) is None and r2.price("X-EN001", NOW) is None
    assert len(calls) == 3                                 # one failed lookup per day, not one per listing
    r2.price("X-EN001", NOW + timedelta(days=1))
    assert len(calls) == 4
