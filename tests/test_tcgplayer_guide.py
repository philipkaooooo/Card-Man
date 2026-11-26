"""The accurate-market-guide layer: settle rules and the pkm_guide write, on fixtures (no network)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from cardman import tcgplayer as t

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
PID = "pk:EN:sv3-223:holo@pkm-sv3-223"


def product(market, asks):
    doc = {"productId": 509980, "productLineId": 3, "productName": "Charizard ex",
           "setName": "SV03: Obsidian Flames", "customAttributes": {"number": "223/197"},
           "marketPrice": market,
           "listings": [{"listingId": i + 1, "price": str(p), "condition": c, "printing": "Holofoil",
                         "language": "English", "quantity": 1} for i, (p, c) in enumerate(asks)]}
    return t.parse_product(doc)


def hist(rows):
    return {"result": [{"skuId": i + 1, "variant": v, "condition": c, "language": "English",
                        "buckets": [{"bucketStartDate": d, "marketPrice": m, "lowSalePrice": lo,
                                     "highSalePrice": hi, "transactionCount": n}]}
                       for i, (v, c, d, m, lo, hi, n) in enumerate(rows)]}


def test_sold_today_beats_the_stale_printed_market():
    p = product("94.59", [("84.99", "Near Mint"), ("45.87", "Damaged")])
    h = hist([("Holofoil", "Near Mint", "2026-11-25", "82.54", "78.98", "84.98", 3)])
    s = t.settle_market(p, h, now=NOW, variant="Holofoil")
    assert str(s["effective"]) == "82.54"
    assert s["effective_source"] == "sold_today"
    assert str(s["nm_low"]) == "84.99"          # the damaged $45.87 ask is not the floor
    assert s["sold_today"].transactions == 3


def test_echo_sale_is_never_a_comp():
    p = product("94.59", [("84.99", "Near Mint")])
    h = hist([("Holofoil", "Near Mint", "2026-11-25", "82.54", "78.98", "84.98", 0)])  # qty/txn 0
    s = t.settle_market(p, h, now=NOW, variant="Holofoil")
    assert s["sold_today"] is None
    assert s["effective_source"] == "market"
    assert str(s["effective"]) == "94.59"


def test_seven_day_nm_median_used_when_market_drifts_over_12pct():
    p = product("100.00", [("99.00", "Near Mint")])
    h = hist([("Holofoil", "Near Mint", f"2026-11-{d}", "80.00", "78.00", "82.00", 2) for d in (20, 21, 22)])
    s = t.settle_market(p, h, now=NOW, variant="Holofoil")
    assert s["effective_source"] == "sold_7d"
    assert str(s["effective"]) == "80.00"


def test_aligned_market_is_kept():
    p = product("100.00", [("99.00", "Near Mint")])
    h = hist([("Holofoil", "Near Mint", f"2026-11-{d}", "95.00", "93.00", "97.00", 2) for d in (20, 21, 22)])
    s = t.settle_market(p, h, now=NOW, variant="Holofoil")
    assert s["effective_source"] == "market"
    assert str(s["effective"]) == "100.00"


def test_nm_ask_floor_ignores_damaged():
    p = product("94.59", [("45.00", "Damaged"), ("84.99", "Near Mint"), ("80.00", "Lightly Played")])
    s = t.settle_market(p, hist([]), now=NOW, variant="Holofoil")
    assert str(s["nm_low"]) == "80.00"          # min of NM/LP, not the damaged low


def _fake_store():
    db = sqlite3.connect(":memory:")
    return SimpleNamespace(db=db)


def _fake_catalog():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE pkm_guide (card_id TEXT, version TEXT, day TEXT, usd REAL, "
                "PRIMARY KEY (card_id, version, day))")
    return SimpleNamespace(con=con, by_id={PID: SimpleNamespace(edition="Holo")})


def test_sync_guide_writes_the_accurate_price_into_pkm_guide(monkeypatch):
    monkeypatch.setattr(t, "xref_product", lambda pid: (509980, "Holofoil"))
    store, catalog = _fake_store(), _fake_catalog()
    det = {"productId": 509980, "productLineId": 3, "productName": "Charizard ex",
           "setName": "SV03: Obsidian Flames", "customAttributes": {"number": "223/197"}, "marketPrice": "94.59"}
    rows = [{"listingId": 1, "price": "84.99", "condition": "Near Mint", "printing": "Holofoil",
             "language": "English", "quantity": 1}]
    h = hist([("Holofoil", "Near Mint", "2026-11-25", "82.54", "78.98", "84.98", 3)])
    res = t.sync_guide(store, catalog, PID, NOW, details=det, listings=(rows, 1), history=h)
    assert res["status"] == "ok"
    assert res["effective"] == "82.54"
    g = catalog.con.execute("SELECT usd, day FROM pkm_guide WHERE card_id='sv3-223' AND version='Holo'").fetchone()
    assert g == (82.54, "2026-11-25")           # catalog.guide(pid) now serves the live price, not $94.59
    comp = t.guide_comp(store, PID)
    assert comp["effective"] == "82.54"
    assert comp["effective_source"] == "sold_today"
    assert comp["product_id"] == 509980


def test_sync_guide_without_xref_writes_nothing(monkeypatch):
    monkeypatch.setattr(t, "xref_product", lambda pid: (None, None))
    store, catalog = _fake_store(), _fake_catalog()
    res = t.sync_guide(store, catalog, PID, NOW)
    assert res["status"] == "noxref"
    assert catalog.con.execute("SELECT count(*) FROM pkm_guide").fetchone()[0] == 0