"""Invariants of the pricing path, one test each (see docs/pricing-method.md). Made-up data only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import pricing
from cardman.evidence import select_comps
from cardman.models import EvidenceType as ET
from cardman.verify import filter_comps
from tests.fixtures import obs

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
P = "p:x@1|ed:1st"


def sale(i, amount, days, **kw):
    when = NOW - timedelta(days=days)
    return obs(i, kw.pop("et", ET.MARKETPLACE_MARKED_SOLD), str(amount), printing=kw.pop("printing", P), when=when, capture_time=when, **kw)


def comps(pool, **kw):
    c, rejected = select_comps(pool, kw.pop("pid", P), as_of=NOW, include_synthetic=True, **kw)
    c, more = filter_comps(c, pool, NOW)
    return c, rejected + more


def test_one_sale_seen_three_ways_counts_once():
    # eBay sold-count unit (checked hours after the sale), the same sale on a saved eBay page, and on 130point.
    unit = sale("u", 100, 0.2, source="ebay_browse:sold_count", listing_id="999", upstream_lineage=("ebay:999:3",))
    page = sale("p", 100, 0.5, source="ebay", listing_id="999", upstream_lineage=("ebay:999",))
    point = sale("t", 100, 0.5, source="130point", listing_id="999", upstream_lineage=("ebay:999",), et=ET.ACCEPTED_OFFER)
    c, _ = comps([unit, page, point])
    assert len(c) == 1


def test_different_units_of_one_multi_copy_listing_are_separate_sales():
    units = [sale(f"u{i}", 5, 1, source="ebay_browse:sold_count", listing_id="77", upstream_lineage=(f"ebay:77:{i}",)) for i in range(4)]
    assert len(comps(units)[0]) == 4


def test_graded_and_raw_never_pool():
    raw = [sale(i, 100, i) for i in range(3)]
    psa = [sale(f"g{i}", 900, i, grade_company="PSA", grade="10") for i in range(3)]
    assert {o.original_amount for o in comps(raw + psa)[0]} == {D(100)}
    assert {o.original_amount for o in comps(raw + psa, grade_company="PSA", grade="10")[0]} == {D(900)}
    assert comps(raw + psa, grade_company="PSA", grade="9")[0] == []


def test_editions_and_printings_never_pool():
    first = [sale(i, 900, i) for i in range(3)]
    unl = [sale(f"n{i}", 200, i, printing="p:x@1|ed:Unlimited") for i in range(3)]
    unconfirmed = [sale(f"q{i}", 500, i, printing_candidates=("p:x@1|ed:1st", "p:x@1|ed:Unlimited")) for i in range(3)]
    other = [sale(f"o{i}", 50, i, printing="p:y@2") for i in range(3)]
    assert {o.original_amount for o in comps(first + unl + unconfirmed + other)[0]} == {D(900)}


def test_every_sale_is_converted_once_at_its_own_dates_rate():
    from cardman.fx import FxRate, FxTable
    from cardman.opportunity import PriceBook

    class Fx:
        def table(self, pairs, moments):
            return FxTable([FxRate("USD", "TWD", D(30), NOW - timedelta(days=10), "t"), FxRate("USD", "TWD", D(33), NOW - timedelta(days=1), "t")],
                           max_age=timedelta(days=30))

    class St:
        def observations_for(self, pid, as_of):
            return [sale(i, 10, d, currency="USD", synthetic_fixture=False) for i, d in ((1, 9), (2, 0.5), (3, 0.4))]
    book = PriceBook(St(), Fx(), {"home_currency": "TWD"}, NOW)
    assert sorted(s.price for s in book.value(P).sales) == [300.0, 330.0, 330.0]


def test_identity_weights_sum_to_one_and_card_shares_too():
    from cardman.intel import analyze
    from tests.test_engine import CAT
    a = analyze("Blue-Eyes White Dragon LOB-EN001 Ultra Rare", CAT)
    assert abs(sum(a.weights.values()) - 1) < 1e-9


def test_one_or_two_sales_never_price_and_few_sales_use_the_lowest():
    s = lambda *p: [pricing.Sale(NOW - timedelta(days=i * 5), float(x)) for i, x in enumerate(p)]
    assert pricing.estimate(s(500), NOW) is None and pricing.estimate(s(500, 600), NOW) is None
    e = pricing.estimate(s(500, 600, 700), NOW)
    assert e.low == 500 and e.mid == 600


def test_one_wild_sale_does_not_move_the_middle():
    base = [pricing.Sale(NOW - timedelta(days=i * 3), 100.0) for i in range(8)]
    wild = base + [pricing.Sale(NOW - timedelta(days=1), 5000.0)]
    assert pricing.estimate(wild, NOW).mid == pricing.estimate(base, NOW).mid == 100.0


def test_card_level_share_never_exceeds_one(tmp_path):
    import json
    from cardman.card_market import ensure_index
    from cardman.store import Store
    st = Store(tmp_path / "s.sqlite")
    st.db.execute("CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
                  "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT)")
    d = {"scenarios": [{"printing_id": "a@1", "weight": 0.5}, {"printing_id": "b@1", "weight": 0.25}, {"printing_id": "c@2", "weight": 0.25}],
         "kinds": [], "all_in_home": 10}
    with st.db:
        st.db.execute("INSERT INTO opportunity VALUES ('k','S','OPEN',1,NULL,0,'[]',NULL,NULL,'fp',?, 't','t')", (json.dumps(d),))
    ensure_index(st)
    shares = dict(st.db.execute("SELECT passcode, card_prob FROM card_listing"))
    assert shares == {"1": 0.75, "2": 0.25}


def test_cross_source_match_is_one_to_one():
    # Two separate eBay sold-count sales at the same price (two listings), one of them also on 130point: two sales, not one.
    point = sale("t", 100, 1, source="130point", listing_id="5", upstream_lineage=("ebay:5",), et=ET.ACCEPTED_OFFER)
    u1 = sale("u1", 100, 1, source="ebay_browse:sold_count", listing_id="5", upstream_lineage=("ebay:5:1",))
    u2 = sale("u2", 100, 1.5, source="ebay_browse:sold_count", listing_id="6", upstream_lineage=("ebay:6:1",))
    assert len(comps([point, u1, u2])[0]) == 2
