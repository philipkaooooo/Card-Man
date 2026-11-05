"""Persistent evidence / decision store: immutability, status events, replay, alerts, invalidation."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cardman.economics import AcquisitionCosts, CeilingInputs, ExitCosts
from cardman.evidence import select_comps
from cardman.identity import IdentityResult
from cardman.models import EvidenceType, IdentityStatus
from cardman.slice import run
from cardman.store import ImmutableViolation, Store, compute_valuation, replay, valuation_inputs
from cardman.workflow import Action, Decision, Stage, evaluate
from tests.fixtures import P_LOB_1ST, obs

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent


def test_observation_is_immutable(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    o = obs(1, EvidenceType.MARKETPLACE_MARKED_SOLD, "1000")
    assert st.add_observation(o) is True
    assert st.add_observation(o) is False                     # identical re-insert is a no-op
    with pytest.raises(ImmutableViolation):
        st.add_observation(replace(o, original_amount=Decimal("900")))
    assert st.original("o1").original_amount == Decimal("1000")


def test_round_trip_keeps_types(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    o = obs(1, EvidenceType.ACCEPTED_OFFER, "1234.50", shipping_amount=Decimal("60"), upstream_lineage=("ebay:9",))
    st.add_observation(o)
    back = Store(tmp_path / "s.sqlite").original("o1")
    assert back == o


def test_cancellation_is_a_status_event_and_respects_decision_time(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    o = obs(1, EvidenceType.MARKETPLACE_MARKED_SOLD, "1000")
    st.add_observation(o, now=NOW - timedelta(days=5))
    st.set_status("o1", "CANCELLED", "buyer did not pay; listing relisted", actor="manual", now=NOW)
    assert st.original("o1").status == "VALID"                                  # source fact untouched
    assert st.effective("o1").status == "CANCELLED"
    assert st.effective("o1", as_of=NOW - timedelta(days=1)).status == "VALID"   # what was known then
    comps, rejected = select_comps([st.effective("o1")], P_LOB_1ST.id, as_of=NOW, include_synthetic=True)
    assert comps == [] and "CANCELLED" in rejected[0][1]
    with pytest.raises(ValueError):
        st.set_status("o1", "INVALID", "", actor="manual")


def test_observations_for_excludes_later_captures(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    st.add_observation(obs(1, EvidenceType.MARKETPLACE_MARKED_SOLD, "1000", when=NOW - timedelta(days=3)))
    st.add_observation(obs(2, EvidenceType.MARKETPLACE_MARKED_SOLD, "1100", when=NOW + timedelta(days=1)))
    assert [o.id for o in st.observations_for(P_LOB_1ST.id, as_of=NOW)] == ["o1"]


def _inputs():
    inp = CeilingInputs(Decimal("0"), AcquisitionCosts(inbound_shipping=Decimal("600")),
                        ExitCosts(platform_fee_pct=Decimal("0.1325"), outbound_shipping=Decimal("300")),
                        Decimal("0.20"))
    return valuation_inputs([Decimal("16000"), Decimal("15000"), Decimal("17500"), Decimal("14800")], inp)


def test_snapshot_replays_exactly(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    vin = _inputs()
    sid = st.save_snapshot(listing_ref="L1", printing_ids=[P_LOB_1ST.id], now=NOW,
                           payload={"valuation_inputs": vin, "valuation": compute_valuation(vin)})
    assert st.save_snapshot(listing_ref="L1", printing_ids=[P_LOB_1ST.id], now=NOW,
                            payload={"valuation_inputs": vin, "valuation": compute_valuation(vin)}) == sid
    r = replay(Store(tmp_path / "s.sqlite"), sid)
    assert r["matches"] and r["identity_valid"]
    assert r["recorded"]["buy_ceiling"] is not None


def test_tampered_snapshot_is_detected(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    vin = _inputs()
    sid = st.save_snapshot(listing_ref="L1", printing_ids=[], payload={"valuation_inputs": vin,
                                                                       "valuation": compute_valuation(vin)})
    with st.db:
        st.db.execute("UPDATE snapshot SET payload_json=replace(payload_json, '16000', '26000') WHERE id=?", (sid,))
    with pytest.raises(ImmutableViolation):
        st.snapshot(sid)


def _qualified_eval(now=NOW):
    ident = IdentityResult(IdentityStatus.RESOLVED, [P_LOB_1ST])
    return evaluate(listing_ref="L1", identity=ident, n_comps=5, min_comps=3, ask_all_in=None,
                    ceiling=Decimal("10000"), ask_price=Decimal("8000"), listing_last_seen=now, now=now,
                    authenticity_ok=True)


def test_alert_once_then_expire(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    ev = _qualified_eval()
    assert ev.stage == Stage.QUALIFIED
    sid = st.save_snapshot(listing_ref="L1", printing_ids=[P_LOB_1ST.id], payload={"x": 1})
    assert st.maybe_alert(ev, sid, NOW) is True
    assert Store(tmp_path / "s.sqlite").maybe_alert(ev, sid, NOW) is False      # survives restart
    assert st.maybe_alert(ev, sid, ev.expires_at) is False
    assert st.expired_alerts(ev.expires_at) == [("L1", sid)]


def test_invalidated_identity_blocks_alert_and_yes(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    ev = _qualified_eval()
    sid = st.save_snapshot(listing_ref="L1", printing_ids=[P_LOB_1ST.id], payload={"x": 2})
    assert st.invalidate_printing(P_LOB_1ST.id, "edition stamp shows Unlimited") == [sid]
    assert st.snapshot_validity(sid)[0] is False
    assert st.maybe_alert(ev, sid, NOW) is False
    with pytest.raises(ValueError):
        st.record_decision(Decision(sid, Action.YES, "buy", NOW))
    st.record_decision(Decision(sid, Action.NO, "wrong printing", NOW))
    assert st.decisions(sid)[0][0] == "NO"


def test_decision_must_reference_a_snapshot(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    with pytest.raises(KeyError):
        st.record_decision(Decision("nope", Action.NO, "x", NOW))
    with pytest.raises(ValueError):
        Decision("x", Action.WAIT, "later", NOW)          # WAIT needs a trigger


def test_retired_identity_in_cache_invalidates_snapshots(tmp_path):
    class FakeCache:
        def is_retired(self, pid): return pid == P_LOB_1ST.id
        def revision(self): return "abc123"
    st = Store(tmp_path / "s.sqlite")
    sid = st.save_snapshot(listing_ref="L1", printing_ids=[P_LOB_1ST.id], payload={"x": 3})
    assert st.invalidate_retired(FakeCache()) == [sid]
    assert st.invalidate_retired(FakeCache()) == []        # recorded once


def test_slice_records_and_replays(tmp_path):
    listing = json.loads((ROOT / "examples" / "demo_listing_SYNTHETIC.json").read_text())
    rep = run(listing, ROOT / "examples" / "demo_observations_SYNTHETIC.csv", NOW, allow_synthetic=True,
              store_path=str(tmp_path / "s.sqlite"))
    sid = rep["status"]["stored_snapshot_id"]
    r = replay(Store(tmp_path / "s.sqlite"), sid)
    assert r["matches"]
    assert str(r["recorded"]["buy_ceiling"]) == rep["buy_ceiling"]["ceiling"]
