"""Does the engine beat a simple price tracker? Checked on what happened next, not on estimates.

For every listing the engine valued (its first snapshot is the decision point), two strategies decide:

* Engine: buy when the exact printing is resolved and the ask is at or under the pay-up-to price.
* Price tracker: buy when the ask is at least `discount` under the median of every sold-looking record
  for the card in the last 90 days, the way a price-guide alert works: no identity check, no lot,
  grade or cancelled-sale cleaning, all candidate printings pooled.

The result of a buy is what it actually resold for (a SOLD outcome on that snapshot) or, failing that,
the median of the clean sales of that exact printing after the decision, inside the holding period.
That second number is a counterfactual from later public sales, labelled as such. When neither exists
the outcome is unknown and the buy is counted as unverifiable rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from .economics import AcquisitionCosts, ExitCosts
from .evidence import select_comps
from .fx import FxTable, StaleRateError
from .models import COMP_ELIGIBLE, EvidenceType

BACKTEST_VERSION = "backtest-0.1.0"
TRACKER_TYPES = set(COMP_ELIGIBLE) | {EvidenceType.AGGREGATE_ESTIMATE}


@dataclass
class Row:
    listing_ref: str
    snapshot_id: str
    at: datetime
    ask: Decimal | None
    engine_buy: bool
    engine_why: str
    tracker_buy: bool
    tracker_why: str
    resale: Decimal | None
    resale_kind: str           # "actual" | "later sales" | "unknown"
    resale_at: datetime | None
    profit: Decimal | None     # if bought at the ask
    all_in: Decimal | None
    hits_target: bool | None


@dataclass
class Score:
    name: str
    buys: int = 0
    known: int = 0
    unverifiable: int = 0
    profit: Decimal = Decimal(0)
    wins: int = 0
    false_positives: int = 0   # bought, lost money
    missed: int = 0            # did not buy, would have hit the target
    capital: Decimal = Decimal(0)
    capital_days: Decimal = Decimal(0)

    @property
    def return_on_capital(self) -> float | None:
        return float(self.profit / self.capital) if self.capital else None

    @property
    def profit_per_capital_month(self) -> float | None:
        return float(self.profit / (self.capital_days / 30)) if self.capital_days else None


@dataclass
class Backtest:
    rows: list[Row]
    engine: Score
    tracker: Score
    discount: Decimal
    home: str
    version: str = BACKTEST_VERSION
    notes: list[str] = field(default_factory=list)


def _costs(snap: dict) -> tuple[AcquisitionCosts, ExitCosts, Decimal, Decimal] | None:
    ci = snap["valuation_inputs"].get("ceiling_inputs")
    if ci:
        return (AcquisitionCosts(**ci["acquisition"]), ExitCosts(**ci["exit"]), ci["target_return"],
                ci["holding_months"])
    c = snap.get("listing", {}).get("costs")
    if not c:
        return None
    D = lambda v: Decimal(str(v))
    return (AcquisitionCosts(**{k: D(v) for k, v in c["acquisition"].items()}),
            ExitCosts(**{k: D(v) for k, v in c["exit"].items()}), D(c["target_return"]), D(c.get("holding_months", "2")))


def run_backtest(store, *, home: str, now: datetime, discount: Decimal = Decimal("0.20"),
                 include_synthetic: bool = False) -> Backtest:
    fx = FxTable(store.fx_rates(), max_age=timedelta(days=7))
    first: dict[str, dict] = {}
    for s in sorted(store.snapshots(limit=10000), key=lambda s: s["created_at"]):
        first.setdefault(s["listing_ref"], s)
    sold = {}
    for ev in store.outcomes():
        if ev["kind"] == "SOLD" and ev["snapshot_id"]:
            sold[ev["snapshot_id"]] = ev
    eng, trk = Score("CardMan"), Score(f"Price tracker ({int(discount * 100)}% under median)")
    rows, notes = [], []
    for ref, meta in first.items():
        snap = store.snapshot(meta["id"])
        rep = snap["report"]
        t = datetime.fromisoformat(meta["created_at"])
        if rep.get("comps") and any(c.get("synthetic") for c in rep["comps"]) and not include_synthetic:
            continue
        costs = _costs(snap)
        ask_raw = rep.get("ask")
        if not ask_raw or costs is None:
            continue
        acq, ex, target, months = costs
        # Costs in a snapshot are in that listing's home currency, so all math happens there and only the
        # results are converted for display.
        cur = snap.get("listing", {}).get("home_currency", "TWD")
        if ask_raw.get("home") is None:
            continue
        ask = Decimal(str(ask_raw["home"]))
        all_in = acq.all_in(ask)
        ceiling = snap["valuation"].get("buy_ceiling")
        resolved = rep["identity"]["status"] == "RESOLVED"
        e_buy = bool(resolved and ceiling is not None and ask <= ceiling)
        e_why = ("identity not resolved" if not resolved else "no price yet" if ceiling is None
                 else "ask at or under pay-up-to" if e_buy else "ask over pay-up-to")
        # Tracker: pooled, uncleaned, last 90 days.
        pids = rep["identity"]["candidates"]
        pool = []
        for pid in pids:
            for o in store.observations_for(pid, as_of=t):
                if (o.evidence_type in TRACKER_TYPES and t - timedelta(days=90) <= o.event_time <= t
                        and (include_synthetic or not o.synthetic_fixture)):
                    try:
                        pool.append(fx.convert(o.original_amount, o.currency, cur, o.event_time))
                    except StaleRateError:
                        pass
        t_med = Decimal(median(pool)) if pool else None
        t_buy = bool(t_med is not None and ask <= t_med * (1 - discount))
        t_why = ("no price history" if t_med is None else
                 f"ask {'under' if t_buy else 'not under'} {int(discount * 100)}% off the median")
        # What it resold for.
        resale, kind, r_at = None, "unknown", None
        if meta["id"] in sold:
            ev = sold[meta["id"]]
            try:
                resale = fx.convert(ev["amount"], ev["currency"], cur, ev["at"])
                kind, r_at = "actual", ev["at"]
            except StaleRateError:
                pass
        if resale is None and len(pids) == 1:
            horizon = t + timedelta(days=float(months) * 30 or 60)
            later, _ = select_comps(store.observations_for(pids[0], as_of=min(horizon, now)), pids[0],
                                    as_of=min(horizon, now), include_synthetic=include_synthetic)
            vals, last = [], None
            for c in later:
                if c.event_time > t:
                    try:
                        vals.append(fx.convert(c.original_amount, c.currency, cur, c.event_time))
                        last = max(last or c.event_time, c.event_time)
                    except StaleRateError:
                        pass
            if vals:
                resale, kind, r_at = Decimal(median(vals)), "later sales", last
        profit = (ex.net(resale) - all_in) if resale is not None else None
        hit = (profit >= target * all_in) if profit is not None else None
        try:      # results in the display currency, at the decision-time rate
            k = fx.convert(Decimal(1), cur, home, t)
        except StaleRateError:
            notes.append(f"{ref}: no {cur}/{home} rate at {t:%Y-%m-%d}; skipped")
            continue
        ask, all_in = ask * k, all_in * k
        resale = resale * k if resale is not None else None
        profit = profit * k if profit is not None else None
        t_med = t_med * k if t_med is not None else None
        rows.append(Row(ref, meta["id"], t, ask, e_buy, e_why, t_buy, t_why, resale, kind, r_at, profit, all_in, hit))
        for sc, buy in ((eng, e_buy), (trk, t_buy)):
            if buy:
                sc.buys += 1
                if profit is None:
                    sc.unverifiable += 1
                    continue
                sc.known += 1
                sc.profit += profit
                sc.capital += all_in
                days = Decimal(max(((r_at or t) - t).days, 1))
                sc.capital_days += all_in * days
                if profit > 0:
                    sc.wins += 1
                else:
                    sc.false_positives += 1
            elif hit:
                sc.missed += 1
    if not rows:
        notes.append("No valued listing with an ask yet: the comparison starts once real listings are valued.")
    return Backtest(rows, eng, trk, discount, home, notes=notes)
