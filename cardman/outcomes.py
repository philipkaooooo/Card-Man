"""What actually happened after a decision, and what the engine should learn from it.

A position is one card you negotiated for, bought, maybe graded, and sold (or wrote off). Its history
is a list of append-only events in the store. Nothing here is estimated: amounts are what you paid and
received, converted to the home currency with the stored rate nearest before each event.

The learning part compares each closed position with the snapshot it was bought on: was the low-end
resale estimate actually beaten, how long did it really take to sell, how far off were grading hopes.
Suggestions are shown with the evidence behind them; nothing changes the model automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from .fx import FxTable, StaleRateError

KINDS = ("OFFER", "COUNTER", "OFFER_DECLINED", "PURCHASE", "COST", "GRADED", "LISTED", "SOLD", "RETURNED",
         "WRITE_OFF", "PASSED", "NOTE", "MISTAKE")
KIND_LABEL = {"OFFER": "Offer sent", "COUNTER": "Seller countered", "OFFER_DECLINED": "Offer declined",
              "PURCHASE": "Bought", "COST": "Extra cost", "GRADED": "Grade back", "LISTED": "Listed for sale",
              "SOLD": "Sold", "RETURNED": "Returned / refunded", "WRITE_OFF": "Written off", "PASSED": "Passed on it",
              "NOTE": "Note", "MISTAKE": "Mistake"}
FX_WINDOW = timedelta(days=7)


@dataclass
class Position:
    name: str
    printing_id: str | None
    snapshot_id: str | None
    state: str                      # NEGOTIATING | HOLDING | SOLD | CLOSED_LOSS | PASSED
    bought_at: datetime | None = None
    sold_at: datetime | None = None
    cost: Decimal = Decimal(0)      # price + every extra cost, home currency
    proceeds: Decimal = Decimal(0)  # sale gross minus fees and shipping, home currency
    sale_gross: Decimal | None = None
    realized: Decimal | None = None
    days_held: int | None = None
    offers: list[tuple[datetime, str, Decimal | None]] = field(default_factory=list)
    grade: str | None = None
    predicted_low: Decimal | None = None      # the snapshot's low-end resale
    predicted_mid: Decimal | None = None
    predicted_months: Decimal | None = None
    predicted_profit: Decimal | None = None   # at the price actually paid, low-end resale
    notes: list[str] = field(default_factory=list)
    fx_problems: list[str] = field(default_factory=list)

    @property
    def return_pct(self) -> Decimal | None:
        return None if self.realized is None or not self.cost else self.realized / self.cost


def _home(fx: FxTable, amount: Decimal, cur: str, home: str, at: datetime, problems: list[str]) -> Decimal:
    try:
        return fx.convert(amount, cur, home, at)
    except StaleRateError as x:
        problems.append(str(x))
        return Decimal(0)


def build_positions(store, *, home: str, now: datetime) -> list[Position]:
    fx = FxTable(store.fx_rates(), max_age=FX_WINDOW)
    events: dict[str, list[dict]] = {}
    for ev in store.outcomes():
        events.setdefault(ev["position"], []).append(ev)
    out = []
    for name, evs in events.items():
        p = Position(name, next((e["printing_id"] for e in evs if e["printing_id"]), None),
                     next((e["snapshot_id"] for e in evs if e["snapshot_id"]), None), "NEGOTIATING")
        paid = None
        for e in evs:
            k, amt = e["kind"], e["amount"]
            val = _home(fx, amt, e["currency"], home, e["at"], p.fx_problems) if amt is not None else None
            if k in ("OFFER", "COUNTER", "OFFER_DECLINED"):
                p.offers.append((e["at"], KIND_LABEL[k], val))
            elif k == "PURCHASE":
                p.bought_at, paid = e["at"], val
                # The engine's ledger records the all-in cost next to the price; count that, not just the price.
                if e["data"].get("all_in_home") not in (None, "") and amt is not None:
                    p.cost += _home(fx, Decimal(str(e["data"]["all_in_home"])), e["currency"], home, e["at"], p.fx_problems)
                else:
                    p.cost += val or 0
                p.state = "HOLDING"
            elif k == "COST":
                p.cost += val or 0
            elif k == "GRADED":
                p.grade = f"{e['data'].get('company', '')} {e['data'].get('grade', '')}".strip() or None
                p.cost += val or 0
            elif k == "SOLD":
                fees = e["data"].get("fees", 0)
                ship = e["data"].get("shipping", 0)
                deduct = _home(fx, Decimal(str(fees)) + Decimal(str(ship)), e["currency"], home, e["at"], p.fx_problems)
                p.sale_gross = val
                p.proceeds += (val or 0) - deduct
                p.sold_at, p.state = e["at"], "SOLD"
            elif k == "RETURNED":
                p.proceeds += val or 0        # refund received back
                p.sold_at, p.state = e["at"], "CLOSED_LOSS"
            elif k == "WRITE_OFF":
                p.sold_at, p.state = e["at"], "CLOSED_LOSS"
            elif k == "PASSED":
                p.state = "PASSED"
            if e["data"].get("text"):
                p.notes.append(f"{KIND_LABEL[k]}: {e['data']['text']}")
        if p.state in ("SOLD", "CLOSED_LOSS"):
            p.realized = p.proceeds - p.cost
        if p.bought_at:
            p.days_held = ((p.sold_at or now) - p.bought_at).days
        if p.snapshot_id:
            _attach_prediction(store, p, paid, fx, home)
        out.append(p)
    order = {"HOLDING": 0, "NEGOTIATING": 1, "SOLD": 2, "CLOSED_LOSS": 3, "PASSED": 4}
    return sorted(out, key=lambda p: (order[p.state], -(p.bought_at or now).timestamp()))


def _attach_prediction(store, p: Position, paid: Decimal | None, fx: FxTable, home: str) -> None:
    """The snapshot's predictions, converted from that listing's home currency at decision time."""
    from .economics import AcquisitionCosts, ExitCosts
    try:
        snap = store.snapshot(p.snapshot_id)
        at = datetime.fromisoformat(store.snapshot_meta(p.snapshot_id)["created_at"])
    except KeyError:
        return
    cur = snap.get("listing", {}).get("home_currency", home)
    try:
        k = fx.convert(Decimal(1), cur, home, at)
    except StaleRateError as x:
        p.fx_problems.append(str(x))
        return
    sc = snap["valuation"]["scenarios"]
    low, mid = sc.get("conservative"), sc.get("base")
    p.predicted_low = low * k if low is not None else None
    p.predicted_mid = mid * k if mid is not None else None
    ci = snap["valuation_inputs"].get("ceiling_inputs")
    if ci:
        p.predicted_months = ci["holding_months"]
        if paid is not None and low is not None:
            paid_snap = paid / k
            p.predicted_profit = (ExitCosts(**ci["exit"]).net(low) - AcquisitionCosts(**ci["acquisition"]).all_in(paid_snap)) * k


@dataclass
class Lesson:
    title: str
    evidence: str
    suggestion: str
    severity: str        # "act" | "watch" | "ok"


@dataclass
class Learning:
    closed: int
    holding: int
    realized: Decimal
    capital_out: Decimal
    win_rate: float | None
    median_days: float | None
    beat_low: int            # sales at or above the predicted low-end resale
    with_prediction: int
    median_error_pct: float | None   # (actual sale - predicted low) / predicted low
    lessons: list[Lesson]


def learn(positions: list[Position]) -> Learning:
    closed = [p for p in positions if p.state in ("SOLD", "CLOSED_LOSS")]
    holding = [p for p in positions if p.state == "HOLDING"]
    realized = sum((p.realized for p in closed), Decimal(0))
    wins = [p for p in closed if p.realized > 0]
    days = [p.days_held for p in closed if p.days_held is not None]
    pred = [p for p in closed if p.predicted_low and p.sale_gross is not None]
    beat = [p for p in pred if p.sale_gross >= p.predicted_low]
    errs = [float((p.sale_gross - p.predicted_low) / p.predicted_low) for p in pred]
    lessons: list[Lesson] = []
    if len(pred) >= 3:
        rate = len(beat) / len(pred)
        if rate < 0.7:
            lessons.append(Lesson("Low-end resale estimate is too optimistic",
                                  f"Only {len(beat)} of {len(pred)} sales reached the predicted low end "
                                  f"(it is meant to be beaten about 8 times in 10).",
                                  "Raise the target return or value from a lower percentile of recent sales.", "act"))
        else:
            lessons.append(Lesson("Low-end resale estimate holds up",
                                  f"{len(beat)} of {len(pred)} sales reached it.", "Keep it.", "ok"))
    slow = [p for p in closed if p.predicted_months is not None and p.days_held is not None
            and p.days_held > float(p.predicted_months) * 30 * 1.5]
    if len(closed) >= 3 and len(slow) * 3 >= len(closed):
        lessons.append(Lesson("Cards take longer to sell than assumed",
                              f"{len(slow)} of {len(closed)} closed positions sold more than 1.5x later than the "
                              "holding period priced in.",
                              "Raise holding_months so capital cost is charged for the real time.", "act"))
    for p in holding:
        if p.predicted_months is not None and p.days_held is not None and p.days_held > float(p.predicted_months) * 30:
            lessons.append(Lesson(f"{p.name} is past its planned holding time",
                                  f"Held {p.days_held} days; the buy assumed {p.predicted_months} months.",
                                  "Re-price it against current sales, or accept the capital cost.", "watch"))
    losses = [p for p in closed if p.realized is not None and p.realized < 0]
    if losses:
        worst = min(losses, key=lambda p: p.realized)
        lessons.append(Lesson(f"{len(losses)} losing position{'s' if len(losses) > 1 else ''}",
                              f"Largest: {worst.name} ({worst.realized:.2f}). " + ("; ".join(worst.notes[:2])),
                              "Record a MISTAKE note saying what was missed, so the pattern shows up here.",
                              "watch"))
    return Learning(len(closed), len(holding), realized, sum((p.cost for p in holding), Decimal(0)),
                    (len(wins) / len(closed)) if closed else None, median(days) if days else None,
                    len(beat), len(pred), median(errs) if errs else None, lessons)
