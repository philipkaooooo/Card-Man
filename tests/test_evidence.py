from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cardman.evidence import dedup, select_comps
from cardman.models import EvidenceType as E
from tests.fixtures import P_LOB_1ST, obs

T = datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_one_sale_through_three_feeds_counts_once():
    a = obs(1, E.MARKETPLACE_MARKED_SOLD, 30000, source="ebay", source_event_id="111")
    b = obs(2, E.AGGREGATE_ESTIMATE, 30000, source="feedA", upstream_lineage=("ebay:111",))
    c = obs(3, E.MARKETPLACE_MARKED_SOLD, 30000, source="feedB", upstream_lineage=("ebay:111",))
    a2 = obs(4, E.MARKETPLACE_MARKED_SOLD, 30000, source="ebay", upstream_lineage=("ebay:111",))
    kept, dropped = dedup([a2, b, c])
    assert len(kept) == 1 and len(dropped) == 2


def test_active_bids_aggregates_claims_are_not_comps():
    rows = [obs(i, et, 1000) for i, et in enumerate([E.ACTIVE_ASKING, E.OBSERVED_BID, E.AUCTION_ENDED_UNSOLD,
                                                       E.AGGREGATE_ESTIMATE, E.SELLER_CLAIM])]
    comps, rej = select_comps(rows, P_LOB_1ST.id, as_of=T, include_synthetic=True)
    assert comps == [] and len(rej) == 5


def test_accepted_offer_amount_kept_separate_from_list_price():
    listing = obs(1, E.ACTIVE_ASKING, 50000, listing_id="L1")
    accepted = obs(2, E.ACCEPTED_OFFER, 41000, listing_id="L1", source_event_id="offer-9")
    comps, _ = select_comps([listing, accepted], P_LOB_1ST.id, as_of=T, include_synthetic=True)
    assert [c.original_amount for c in comps] == [Decimal(41000)]


def test_lot_grade_mismatch_and_synthetic_excluded():
    rows = [obs(1, E.MARKETPLACE_MARKED_SOLD, 9000, is_lot=True),
            obs(2, E.MARKETPLACE_MARKED_SOLD, 90000, grade_company="PSA", grade="10")]
    comps, rej = select_comps(rows, P_LOB_1ST.id, as_of=T, include_synthetic=True)
    assert comps == []
    assert select_comps([obs(3, E.MARKETPLACE_MARKED_SOLD, 1)], P_LOB_1ST.id, as_of=T)[0] == []


def test_same_slab_sold_twice_on_different_dates_is_two_sales():
    s1 = obs(1, E.MARKETPLACE_MARKED_SOLD, 80000, listing_id="L", cert_number="123", when=T - timedelta(days=90))
    s2 = obs(2, E.MARKETPLACE_MARKED_SOLD, 85000, listing_id="L2", cert_number="123", when=T - timedelta(days=10))
    assert len(dedup([s1, s2])[0]) == 2


def test_relisted_item_is_not_double_counted_when_same_event():
    a = obs(1, E.MARKETPLACE_MARKED_SOLD, 1000, listing_id="L9")
    b = obs(2, E.MARKETPLACE_MARKED_SOLD, 1000, listing_id="L9")
    assert len(dedup([a, b])[0]) == 1


def test_no_future_leakage_and_max_ten():
    rows = [obs(i, E.MARKETPLACE_MARKED_SOLD, 1000 + i, listing_id=f"L{i}", when=T - timedelta(days=i))
            for i in range(15)]
    rows.append(obs(99, E.MARKETPLACE_MARKED_SOLD, 1, listing_id="F", when=T + timedelta(days=1)))
    comps, _ = select_comps(rows, P_LOB_1ST.id, as_of=T, include_synthetic=True)
    assert len(comps) == 10 and comps[0].id == "o0"
