from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from cardman.identity import ListingSignals, resolve
from cardman.workflow import Action, AlertLog, Decision, Stage, evaluate
from tests.fixtures import CATALOG

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
GOOD = resolve(ListingSignals(title="Blue-Eyes White Dragon", printed_set_code="LOB-EN001", verified_edition="1st"), CATALOG)
AMBIG = resolve(ListingSignals(title="Blue-Eyes White Dragon", printed_set_code="LOB-EN001"), CATALOG)


def ev(identity=GOOD, **kw):
    base = dict(listing_ref="ebay:1", identity=identity, n_comps=5, min_comps=3, ask_all_in=D(9000),
                ceiling=D(10000), ask_price=D(8000), listing_last_seen=NOW, now=NOW, authenticity_ok=True)
    base.update(kw)
    return evaluate(**base)


def test_all_gates_pass_qualifies():
    assert ev().stage == Stage.QUALIFIED


def test_wrong_or_ambiguous_printing_blocks_qualified():
    e = ev(identity=AMBIG)
    assert e.stage == Stage.SPECTATE and any(r.startswith("identity") for r in e.reasons)


def test_provider_outage_means_no_comps_not_zero_price():
    e = ev(n_comps=0, ceiling=None)
    assert e.stage == Stage.SPECTATE and e.buy_ceiling is None


def test_stale_listing_and_unreviewed_authenticity_block():
    assert ev(listing_last_seen=NOW - timedelta(days=2)).stage != Stage.QUALIFIED
    assert ev(authenticity_ok=None).stage != Stage.QUALIFIED


def test_no_duplicate_alerts_and_expiry():
    log, e = AlertLog(), ev()
    assert log.maybe_alert(e, NOW) and not log.maybe_alert(e, NOW)
    assert log.expired(NOW + timedelta(hours=7)) == [f"ebay:1:{e.snapshot_id}"]
    assert not log.maybe_alert(ev(), NOW + timedelta(hours=7))


def test_wait_needs_trigger_and_yes_is_not_a_purchase():
    with pytest.raises(ValueError):
        Decision("s", Action.WAIT, "price high", NOW)
    d = Decision("s", Action.YES, "contact seller", NOW)
    assert not hasattr(d, "purchase") and not hasattr(d, "bid")
