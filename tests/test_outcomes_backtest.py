"""Outcome ledger, learning from closed positions, and the engine vs a simple price tracker."""
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest

from cardman.backtest import run_backtest
from cardman.fx import FxRate
from cardman.models import EvidenceType
from cardman.outcomes import build_positions, learn
from cardman.slice import run
from cardman.store import Store
from tests.fixtures import obs
from tests.test_store import NOW, ROOT

DEMO = ROOT / "examples" / "demo_observations_SYNTHETIC.csv"
PID = "p:LOB-EN001:UR:1st"


def listing(ref, ask):
    l = json.loads((ROOT / "examples" / "demo_listing_SYNTHETIC.json").read_text())
    l.update(listing_ref=ref, ask={"amount": ask, "currency": "USD"})
    return l


@pytest.fixture
def st(tmp_path):
    db = str(tmp_path / "s.sqlite")
    sids = {ask: run(listing(f"l{ask}", ask), DEMO, NOW, allow_synthetic=True, store_path=db)["status"]["stored_snapshot_id"]
            for ask in ("480", "590", "700")}
    s = Store(db)
    s.add_fx(FxRate("USD", "TWD", D("32"), NOW + timedelta(days=19), "test"), now=NOW)
    s.add_observation(obs("later", EvidenceType.MARKETPLACE_MARKED_SOLD, "800", printing=PID, currency="USD",
                          when=NOW + timedelta(days=20)), now=NOW + timedelta(days=20))
    s.sids = sids
    return s


def test_rates_from_runs_are_kept_in_the_store(st):
    assert {(r.base, r.quote) for r in st.fx_rates()} == {("USD", "TWD")}
    assert len(st.fx_rates()) == 4


def test_engine_vs_tracker_on_later_sales(st):
    bt = run_backtest(st, home="USD", now=NOW + timedelta(days=40), include_synthetic=True)
    by = {r.listing_ref: r for r in bt.rows}
    assert by["l480"].engine_buy and not by["l590"].engine_buy and not by["l700"].engine_buy
    assert all(r.tracker_buy for r in bt.rows)                      # all within 20% of a $900 median
    assert all(r.resale_kind == "later sales" and r.resale == D("800") for r in bt.rows)
    assert by["l700"].profit < 0 and by["l480"].hits_target
    assert bt.engine.buys == 1 and bt.engine.false_positives == 0 and bt.engine.profit > 0
    assert bt.tracker.buys == 3 and bt.tracker.false_positives == 1
    assert bt.engine.return_on_capital > bt.tracker.return_on_capital


def test_backtest_skips_synthetic_unless_asked(st):
    assert run_backtest(st, home="USD", now=NOW + timedelta(days=40)).rows == []


def test_actual_sale_beats_counterfactual(st):
    sid = st.sids["480"]
    st.add_outcome("BEWD LOB 1st", "PURCHASE", NOW + timedelta(hours=2), amount=D("480"), currency="USD",
                   snapshot_id=sid, printing_id=PID)
    st.add_outcome("BEWD LOB 1st", "SOLD", NOW + timedelta(days=30), amount=D("1000"), currency="USD",
                   snapshot_id=sid, data={"fees": "132.5", "shipping": "10"})
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW + timedelta(days=29), "test"), now=NOW)
    bt = run_backtest(st, home="USD", now=NOW + timedelta(days=40), include_synthetic=True)
    r = next(r for r in bt.rows if r.listing_ref == "l480")
    assert r.resale_kind == "actual" and r.resale == D("1000")


def test_position_lifecycle_and_prediction(st):
    sid = st.sids["480"]
    st.add_outcome("BEWD", "OFFER", NOW, amount=D("450"), currency="USD", snapshot_id=sid)
    st.add_outcome("BEWD", "COUNTER", NOW + timedelta(hours=1), amount=D("480"), currency="USD")
    st.add_outcome("BEWD", "PURCHASE", NOW + timedelta(hours=2), amount=D("480"), currency="USD", printing_id=PID)
    st.add_outcome("BEWD", "COST", NOW + timedelta(hours=2), amount=D("20"), currency="USD", data={"text": "shipping"})
    p = build_positions(st, home="USD", now=NOW + timedelta(days=5))[0]
    assert p.state == "HOLDING" and p.cost == D("500") and len(p.offers) == 2 and p.days_held == 4
    assert p.predicted_low == D("820")                         # snapshot was in TWD, shown in USD
    assert p.predicted_profit is not None and p.predicted_profit > 0
    st.add_outcome("BEWD", "SOLD", NOW + timedelta(days=30), amount=D("700"), currency="USD",
                   data={"fees": "92.75", "shipping": "10"})
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW + timedelta(days=29), "test"), now=NOW)
    p = build_positions(st, home="USD", now=NOW + timedelta(days=40))[0]
    assert p.state == "SOLD" and p.realized == D("700") - D("102.75") - D("500")
    assert p.sale_gross == D("700")


def test_learning_flags_optimistic_estimates(st):
    for i in range(3):
        name = f"pos{i}"
        st.add_outcome(name, "PURCHASE", NOW, amount=D("480"), currency="USD", snapshot_id=st.sids["480"])
        st.add_outcome(name, "SOLD", NOW + timedelta(days=150), amount=D("600"), currency="USD", data={"fees": "80"})
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW + timedelta(days=149), "test"), now=NOW)
    L = learn(build_positions(st, home="USD", now=NOW + timedelta(days=160)))
    assert L.closed == 3 and L.with_prediction == 3
    titles = [l.title for l in L.lessons]
    assert L.beat_low == 0 and L.median_error_pct < 0          # $600 sales against an $820 low end
    assert "Low-end resale estimate is too optimistic" in titles
    assert "Cards take longer to sell than assumed" in titles


def test_outcome_kinds_are_checked(st):
    with pytest.raises(ValueError):
        st.add_outcome("x", "BOUGHT", NOW)
    with pytest.raises(ValueError):
        st.add_outcome("x", "PURCHASE", NOW, amount=D("1"))
    with pytest.raises(KeyError):
        st.add_outcome("x", "PURCHASE", NOW, amount=D("1"), currency="USD", snapshot_id="nope")


def test_one_outcome_table_for_the_results_page_and_the_engine(st):
    from cardman.ledger import Ledger
    L = Ledger(st.path, home="USD")
    # Engine-side purchase and sale show up as a position on the Results page...
    b = L.add("PURCHASE", {"printing_id": PID, "price_home": 400, "all_in_home": 420, "predicted_resale_home": 900},
              at=NOW, listing_key="ebay:1")
    L.add("SALE", {"purchase_id": b, "gross_home": 800, "net_home": 700}, at=NOW + timedelta(days=10))
    # ...and a Results-page position counts in the engine's calibration.
    st.add_outcome("UI #1", "PURCHASE", NOW, amount=D("480"), currency="USD", snapshot_id=st.sids["480"])
    st.add_outcome("UI #1", "SOLD", NOW + timedelta(days=20), amount=D("700"), currency="USD", data={"fees": "100"})
    pos = {p.name: p for p in build_positions(st, home="USD", now=NOW + timedelta(days=30))}
    assert pos[b].state == "SOLD" and pos[b].realized == D("700") - D("420")
    assert {e["id"] for e in L.events("PURCHASE")} == {b, "UI #1"}
    cal = L.calibration(NOW + timedelta(days=30))
    assert cal.n_sales == 2 and cal.realized_profit == pytest.approx(280 + 120)
