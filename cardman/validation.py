"""Does the engine beat simple price tracking on what really happened?

Every strategy sees one listing at a time, with only what was known then, and pays for its own speed: a slow strategy
finds the listing gone. Real results come from your ledger; where no purchase exists, the next sale of that exact printing
stands in and the row is labelled counterfactual. No verdict is given until enough real outcomes exist.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from . import pricing
from .evidence import select_comps
from .ledger import Ledger
from .models import COMP_ELIGIBLE
from .opportunity import PriceBook, costs_for
from .verify import assess, filter_comps

HORIZON = timedelta(days=120)
MIN_REAL_FOR_VERDICT = 20


@dataclass
class StrategyResult:
    name: str
    description: str
    delay_hours: float
    considered: int = 0
    bought: int = 0
    missed_gone: int = 0
    passed_on_winners: int = 0
    no_exit_yet: int = 0
    losers: int = 0
    real_outcomes: int = 0
    counterfactual_outcomes: int = 0
    net_profit: float = 0.0
    capital_days: float = 0.0
    fees_paid: float = 0.0

    @property
    def false_positive_rate(self) -> float | None:
        done = self.real_outcomes + self.counterfactual_outcomes
        return self.losers / done if done else None

    @property
    def annualized_on_capital(self) -> float | None:
        return self.net_profit / self.capital_days * 365 if self.capital_days else None

    def to_dict(self) -> dict:
        return {**asdict(self), "false_positive_rate": self.false_positive_rate, "annualized_on_capital": self.annualized_on_capital}


def _median_or_none(xs):
    return statistics.median(xs) if xs else None


def run_backtest(engine, as_of: datetime, *, include_synthetic: bool = False, horizon: timedelta = HORIZON) -> dict:
    db, store, profile, catalog = engine.db, engine.store, engine.profile, engine.catalog
    fam_of = {p.id: p.family_id for p in catalog.printings}
    ledger = Ledger(engine.store_path)
    buys_by_listing = {b["listing_key"]: b for b in ledger.events("PURCHASE", as_of) if b.get("listing_key")}
    sale_of = {s["purchase_id"]: s for s in ledger.events("SALE", as_of)}
    grade_cost: dict[str, float] = {}
    for g in ledger.events("GRADING_RESULT", as_of):
        grade_cost[g["purchase_id"]] = grade_cost.get(g["purchase_id"], 0.0) + float(g["cost_home"])
    book_at: dict[str, PriceBook] = {}

    strategies = {
        "engine": StrategyResult("Engine", "Exact printing, verified sales, all costs, risk-adjusted max buy.", 0.5),
        "exact_tracker": StrategyResult("Exact-print tracker", "Buys 20% under the median sale of the exact printing. Ignores costs and evidence quality.", 6),
        "name_tracker": StrategyResult("Card-name tracker", "Buys 20% under the median sale of the card name across all printings. The usual price-guide view.", 24),
    }
    rows = db.execute("SELECT o.key, o.printing_id, l.first_seen, l.ended_at, l.data_json FROM opportunity o JOIN listing l ON l.key=o.key WHERE l.kind IN ('ask','bid','auction') AND l.first_seen<=?",
                      (as_of.isoformat(),)).fetchall()
    skipped_unidentified = 0
    for key, stored_pid, first_seen, ended_at, data_json in rows:
        r = engine.listing_from_row(key)
        data = json.loads(data_json)
        if data.get("synthetic") and not include_synthetic:
            continue
        pid = stored_pid or r.printing_id
        if not pid:
            skipped_unidentified += 1
            continue
        t0 = datetime.fromisoformat(first_seen)
        book = book_at.setdefault(first_seen, PriceBook(store, engine.fx, profile, t0))
        try:
            ask = book.to_home(r.price, r.currency, t0)
        except Exception:
            continue
        acq, ex = costs_for(profile, r.market)
        pool = store.observations_for(pid, as_of=t0)
        comps, _ = select_comps(pool, pid, grade_company=r.grade_company, grade=r.grade, as_of=t0, include_synthetic=include_synthetic)
        raw_homes = []
        for c in comps:
            try:
                raw_homes.append(float(book.to_home(c.original_amount, c.currency, c.event_time)))
            except Exception:
                pass
        verified, _ = filter_comps(comps, pool, t0)
        trust = assess(pool, t0)
        sales = []
        for c in verified:
            try:
                sales.append(pricing.Sale(c.event_time, float(book.to_home(c.original_amount, c.currency, c.event_time)), trust[c.id].trust if c.id in trust else 1.0))
            except Exception:
                pass
        est = pricing.estimate(sales, t0)
        engine_buy = False
        if est:
            rv = pricing.risk_view(sales, est, ask, acq, ex, target_return=Decimal(str(profile["target_return"])), capital_cost_monthly=Decimal(str(profile["capital_cost_monthly"])))
            engine_buy = rv.max_buy is not None and ask <= rv.max_buy and bool(stored_pid)

        family_prices = []
        for other in catalog.by_family.get(fam_of.get(pid.split("|")[0], ""), []):
            for o in store.observations_for(other.id, as_of=t0):
                if o.evidence_type in COMP_ELIGIBLE and t0 - o.event_time <= timedelta(days=90) and (include_synthetic or not o.synthetic_fixture):
                    try:
                        family_prices.append(float(book.to_home(o.original_amount, o.currency, o.event_time)))
                    except Exception:
                        pass
        exact_med, name_med = _median_or_none(raw_homes), _median_or_none(family_prices)
        decisions = {"engine": engine_buy, "exact_tracker": exact_med is not None and float(ask) < 0.8 * exact_med,
                     "name_tracker": name_med is not None and float(ask) < 0.8 * name_med}

        # What happens after: the real purchase if you made one, else the next sale of this printing.
        purchase = buys_by_listing.get(key)
        real = None
        if purchase and purchase["id"] in sale_of:
            s = sale_of[purchase["id"]]
            cost = float(purchase["all_in_home"]) + grade_cost.get(purchase["id"], 0.0)
            days = (datetime.fromisoformat(s["at"]) - datetime.fromisoformat(purchase["at"])).total_seconds() / 86400
            real = (float(s["net_home"]), cost, days)
        later = sorted((o for o in store.observations_for(pid, as_of=as_of) if o.evidence_type in COMP_ELIGIBLE and o.status == "VALID"
                        and t0 < o.event_time <= t0 + horizon and o.printing_candidates == (pid,) and (o.grade_company, o.grade) == (r.grade_company, r.grade)
                        and (include_synthetic or not o.synthetic_fixture)), key=lambda o: o.event_time)
        cf = None
        if later:
            first = later[0]
            try:
                gross = book.to_home(first.original_amount, first.currency, first.event_time)
                cf = (float(ex.net(gross)), float(acq.all_in(ask)), (first.event_time - t0).total_seconds() / 86400, float(gross - ex.net(gross)))
            except Exception:
                cf = None
        target = float(profile["target_return"])
        for k, S in strategies.items():
            S.considered += 1
            cost_cf = float(acq.all_in(ask))
            would_win = cf is not None and cf[0] - cf[1] >= cf[1] * target
            if not decisions[k]:
                S.passed_on_winners += int(would_win)
                continue
            if ended_at and datetime.fromisoformat(ended_at) < t0 + timedelta(hours=S.delay_hours):
                S.missed_gone += 1
                continue
            S.bought += 1
            if real and k == "engine":
                net, cost, days = real
                S.real_outcomes += 1
            elif cf:
                net, cost, days = cf[0], cf[1], cf[2]
                S.fees_paid += cf[3]
                S.counterfactual_outcomes += 1
            else:
                S.no_exit_yet += 1
                S.capital_days += cost_cf * horizon.days
                continue
            S.net_profit += net - cost
            S.capital_days += cost * max(days, 1.0)
            S.losers += int(net - cost < 0)
    results = {k: v.to_dict() for k, v in strategies.items()}
    real_n = strategies["engine"].real_outcomes
    verdict = (f"Not enough real outcomes: {real_n} of {MIN_REAL_FOR_VERDICT} needed. Numbers below are counterfactual and do not prove an edge."
               if real_n < MIN_REAL_FOR_VERDICT else
               ("Engine leads on real outcomes." if results["engine"]["net_profit"] > max(results["exact_tracker"]["net_profit"], results["name_tracker"]["net_profit"])
                else "Engine does NOT lead simple tracking on these outcomes."))
    return {"as_of": as_of.isoformat(), "listings": len(rows), "skipped_unidentified": skipped_unidentified, "real_outcomes": real_n,
            "verdict": verdict, "has_verdict": real_n >= MIN_REAL_FOR_VERDICT, "strategies": results}
