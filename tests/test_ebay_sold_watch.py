"""Sold counts from eBay's Browse API, on SYNTHETIC item payloads; the capture bookmark and route."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman.ebay_browse import SourceUnavailable
from cardman.ebay_sold_watch import check
from cardman.models import EvidenceType as ET
from tests.test_engine import make

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


class FakeBrowse:
    """Answers getItemByLegacyId from a dict that the test changes between passes."""
    marketplace = "EBAY_US"

    def __init__(self, items):
        self.items, self.calls = items, 0

    def token(self):
        return "t"

    def _request(self, req):
        self.calls += 1
        iid = req.full_url.rsplit("=", 1)[1]
        if iid not in self.items:
            raise SourceUnavailable("eBay HTTP 404")
        return json.dumps(self.items[iid]).encode()


def item(sold, price="10.00", options=("FIXED_PRICE",), title="Blue-Eyes White Dragon LOB-EN001 Ultra Rare"):
    return {"itemId": "v1|x|0", "title": title, "buyingOptions": list(options), "price": {"value": price, "currency": "USD"},
            "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK", "estimatedSoldQuantity": sold}],
            "seller": {"username": "SYNTHETIC-SELLER", "feedbackScore": 5}, "itemWebUrl": "https://www.ebay.com/itm/1"}


def tracked(e, ids):
    with e.db:
        for i in ids:
            e.db.execute("INSERT INTO listing (key, source, listing_id, market, title, url, seller, kind, price, currency, ends_at, first_seen, last_seen, "
                         "ended_at, fingerprint, data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"ebay_browse:EBAY_US:{i}", "ebay_browse:EBAY_US", i, "INTL", "Blue-Eyes White Dragon LOB-EN001", "", None, "ask", "10",
                          "USD", None, NOW.isoformat(), NOW.isoformat(), None, "fp", "{}"))


def sales(e):
    return e.store.db.execute("SELECT id, evidence_type FROM observation WHERE source='ebay_browse:sold_count' ORDER BY id").fetchall()


def test_first_check_is_a_baseline_then_new_units_are_recorded_once(tmp_path):
    e = make(tmp_path)
    tracked(e, ["1"])
    b = FakeBrowse({"1": item(27)})
    r = check(e, NOW, browse=b)
    assert (r.checked, r.exposing_sold_count, r.sales_recorded) == (1, 1, 0)        # 27 earlier sales: no date, no price
    b.items["1"] = item(30)
    r = check(e, NOW + timedelta(hours=7), browse=b)
    assert r.sales_recorded == 3
    ids = [i for i, _ in sales(e)]
    assert ids == ["ebay_qty:1:28", "ebay_qty:1:29", "ebay_qty:1:30"]
    o = e.store.original("ebay_qty:1:28")
    assert o.original_amount == D("10.00") and o.evidence_type == ET.MARKETPLACE_MARKED_SOLD and o.seller_ref is None
    r = check(e, NOW + timedelta(hours=14), browse=b)                                # same count again: nothing new
    assert r.sales_recorded == 0 and len(sales(e)) == 3


def test_price_change_makes_those_sales_unknown(tmp_path):
    e = make(tmp_path)
    tracked(e, ["1"])
    b = FakeBrowse({"1": item(5, "10.00")})
    check(e, NOW, browse=b)
    b.items["1"] = item(7, "12.00")
    r = check(e, NOW + timedelta(hours=7), browse=b)
    assert r.sales_recorded == 0 and r.skipped_price_changed == 1 and sales(e) == []


def test_best_offer_listings_only_give_an_upper_bound(tmp_path):
    e = make(tmp_path)
    tracked(e, ["1"])
    b = FakeBrowse({"1": item(1, options=("FIXED_PRICE", "BEST_OFFER"))})
    check(e, NOW, browse=b)
    b.items["1"] = item(2, options=("FIXED_PRICE", "BEST_OFFER"))
    r = check(e, NOW + timedelta(hours=7), browse=b)
    assert r.upper_bounds_recorded == 1 and r.sales_recorded == 0
    assert sales(e) == [("ebay_qty:1:2", "SELLER_CLAIM")]


def test_a_listing_that_disappears_is_not_a_sale_and_is_not_checked_again(tmp_path):
    e = make(tmp_path)
    tracked(e, ["1"])
    b = FakeBrowse({"1": item(3)})
    check(e, NOW, browse=b)
    del b.items["1"]
    r = check(e, NOW + timedelta(hours=7), browse=b)
    assert r.gone == 1 and sales(e) == []
    calls = b.calls
    check(e, NOW + timedelta(hours=14), browse=b)
    assert b.calls == calls


def test_auctions_and_budget_and_daily_cap(tmp_path, monkeypatch):
    e = make(tmp_path)
    tracked(e, [str(i) for i in range(10)])
    b = FakeBrowse({str(i): item(1, options=("AUCTION",)) for i in range(10)})
    r = check(e, NOW, browse=b, budget=4)
    assert r.checked == 4 and r.exposing_sold_count == 0
    monkeypatch.setattr("cardman.ebay_sold_watch.DAILY_CAP", 6)
    r = check(e, NOW, browse=b, budget=4)
    assert r.checked == 2                                                          # 4 + 2 = the day's cap


def test_seller_is_not_kept_in_the_raw_copy(tmp_path):
    e = make(tmp_path)
    tracked(e, ["1"])
    check(e, NOW, browse=FakeBrowse({"1": item(3)}))
    blob = b"".join(p.read_bytes() for p in (tmp_path / "raw").rglob("*") if p.is_file())
    assert b"SYNTHETIC-SELLER" not in blob and b"estimatedSoldQuantity" in blob


def test_bookmark_posts_the_page_with_the_key():
    from cardman.capture import bookmarklet, sold_search_links
    js = bookmarklet(8766, "KEY123")
    assert js.startswith("javascript:") and "http://127.0.0.1:8766/capture" in js and '"KEY123"' in js and "outerHTML" in js
    links = dict(sold_search_links("Blue-Eyes White Dragon", "青眼の白龍", "LOB-EN001"))
    assert "LH_Sold=1" in links["eBay sold"] and "_ipg=240" in links["eBay sold"] and "status=sold_out" in links["Mercari 売り切れ"]


def test_rechecks_come_before_new_listings_so_sales_appear_early(tmp_path):
    e = make(tmp_path)
    tracked(e, ["old"])
    b = FakeBrowse({"old": item(10)})
    check(e, NOW, browse=b, budget=3)                                   # baseline for "old"
    tracked(e, [f"n{i}" for i in range(10)])
    for i in range(10):
        b.items[f"n{i}"] = item(1)
    b.items["old"] = item(12)
    r = check(e, NOW + timedelta(hours=7), browse=b, budget=3)          # tiny budget: the recheck must still happen
    assert r.sales_recorded == 2 and r.checked == 3


def test_home_lists_recorded_sales_without_crashing(tmp_path):
    from cardman.ui_home import view_home
    e = make(tmp_path)
    tracked(e, ["1"])
    b = FakeBrowse({"1": item(3)})
    check(e, NOW, browse=b)
    b.items["1"] = item(4)
    check(e, NOW + timedelta(hours=7), browse=b)
    page = view_home(e.store, None, NOW + timedelta(hours=8), "USD", "market")
    assert "Recently sold" in page and "$10" in page and "version not known" in page
