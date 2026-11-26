"""The spread scanner: the engine's own identification and pricing on current asks, costs on the right side, and every
failing or empty source said out loud. No network: the eBay search is replaced by recorded listings."""
from decimal import Decimal as D

from cardman.market_sources import RawListing
from cardman.spread import render, scan
from tests.test_engine import LOB_1ST, NOW, SALES, TITLE, drop, make


def engine_with_sales(tmp_path):
    e = make(tmp_path)
    drop(tmp_path, "sales.jsonl", SALES)
    e.tick(NOW)
    return e


def ask(lid, usd, title=TITLE, **kw):
    return RawListing(source="ebay_browse:EBAY_US", listing_id=lid, title=title, price=D(usd), currency="USD", kind="ask",
                      url=f"https://example.test/{lid}", shipping=D("5"), **kw)


def test_spread_is_value_after_selling_costs_minus_total_to_you(tmp_path):
    e = engine_with_sales(tmp_path)
    rep = scan([TITLE], engine=e, now=NOW, search=lambda eng, q, g, now: ([ask("a1", 600)], []))[0]
    r = rep.rows[0]
    assert "LOB-EN001" in r.version and r.n_sales >= 5 and r.value and r.total
    assert r.total >= 605                         # buying costs are added to the price, never a fee taken off it
    assert r.spread is not None and r.spread < r.value - r.total   # selling fees and shipping come off the sale side
    assert r.decision in ("BUY", "CHECK", "WATCH", "PASS") and not rep.warnings


def test_graded_and_failing_sources_are_labelled_not_hidden(tmp_path):
    e = engine_with_sales(tmp_path)
    graded = ask("g1", 900, title=TITLE + " PSA 10")
    rep = scan([TITLE], engine=e, now=NOW, search=lambda eng, q, g, now: ([graded], []))[0]
    assert rep.rows[0].label.startswith("graded PSA")
    def boom(*a):
        raise TimeoutError("eBay did not answer")
    rep = scan([TITLE], engine=e, now=NOW, search=boom)[0]
    assert rep.rows == [] and rep.warnings == ["eBay: failed (TimeoutError: eBay did not answer)"]
    rep = scan([TITLE], engine=e, now=NOW, search=lambda *a: ([], []))[0]
    assert rep.warnings == ["eBay: no listings found for this search"]
    out = render([rep], lambda v: "-" if v is None else f"${v:,.0f}", "USD")
    assert "! eBay: no listings found" in out


def test_no_keys_means_a_warning_not_an_empty_success(tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    e = engine_with_sales(tmp_path)
    rep = scan([TITLE], engine=e, now=NOW)[0]
    assert rep.rows == [] and rep.warnings[0].startswith("eBay: not searched")
