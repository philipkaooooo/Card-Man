"""Quantitative model on one printing's verified sales: what would it really resell for, how fast, at what risk.

Sold prices only. Fewer sales mean wider, more cautious numbers or no number at all. Trend can pull the estimate
down but never up: the model does not pay for growth it merely extrapolates.
"""
from __future__ import annotations

import hashlib
import math
import random
import statistics
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from .economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling

HALF_LIFE_DAYS = 90.0
LIQUIDITY_WINDOW_DAYS = 180.0
MIN_SALES = 3
N_SIMS = 2000


@dataclass(frozen=True)
class Sale:
    when: datetime
    price: float       # home currency
    trust: float = 1.0


@dataclass(frozen=True)
class PriceEstimate:
    n: int
    effective_n: float
    low: float           # realistic low-end resale
    mid: float
    high: float
    ci_low: float        # bootstrap interval on mid
    ci_high: float
    dispersion: float    # robust spread, share of mid
    trend_monthly: float | None
    trend_note: str
    sales_per_month: float
    days_since_last: float
    method: str


def _wq(pairs: list[tuple[float, float]], q: float) -> float:
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    run = 0.0
    for v, w in pairs:
        run += w
        if run >= q * total:
            return v
    return pairs[-1][0]


def _theil_sen(points: list[tuple[float, float]]) -> float | None:
    slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points) for x2, y2 in points[i + 1:] if x2 != x1]
    return statistics.median(slopes) if slopes else None


def estimate(sales: list[Sale], as_of: datetime, *, bias: float = 1.0) -> PriceEstimate | None:
    """`bias` is the learned realized/predicted ratio from your own outcomes (see ledger.calibration)."""
    usable = [s for s in sales if s.when <= as_of and s.price > 0]
    if len(usable) < MIN_SALES:
        return None
    age = lambda s: max(0.0, (as_of - s.when).total_seconds() / 86400)
    pairs = [(s.price, s.trust * 0.5 ** (age(s) / HALF_LIFE_DAYS)) for s in usable]
    wsum = sum(w for _, w in pairs)
    eff = wsum ** 2 / sum(w * w for _, w in pairs)
    mid = _wq(pairs, 0.5)

    slope = None
    note = "too few sales or too short a span for a trend"
    span = max(age(s) for s in usable) - min(age(s) for s in usable)
    if len(usable) >= 5 and span >= 30:
        slope = _theil_sen([(-age(s) / 30.0, math.log(s.price)) for s in usable])
        note = f"{(math.exp(slope) - 1) * 100:+.1f}% per month (robust fit)"

    if len(usable) < 6:
        low, high, method = min(s.price for s in usable), mid, "sparse: lowest sale / recency median"
    else:
        low, high, method = _wq(pairs, 0.2), _wq(pairs, 0.75), "recency-weighted p20 / p50 / p75"
    recent_n = sum(1 for s in usable if age(s) <= LIQUIDITY_WINDOW_DAYS)
    pace = recent_n * 30.0 / min(LIQUIDITY_WINDOW_DAYS, max(30.0, max(age(s) for s in usable)))
    # A falling market is priced where it is heading by the time this copy sells (expected wait at the observed pace,
    # at least a week, at most two months); a rising one is not paid for. A fast seller is barely exposed to the trend.
    horizon = min(2.0, max(0.25, 1.0 / pace)) if pace > 0 else 2.0
    drift = math.exp(min(0.0, slope or 0.0) * horizon)

    seed = int(hashlib.sha256(repr(sorted((s.when.isoformat(), s.price) for s in usable)).encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    vals, wts = [v for v, _ in pairs], [w for _, w in pairs]
    boots = sorted(_wq(list(zip(rng.choices(vals, wts, k=len(vals)), [1.0] * len(vals))), 0.5) for _ in range(300))
    mad = statistics.median(abs(v - statistics.median(vals)) for v in vals)
    recent = [s for s in usable if age(s) <= LIQUIDITY_WINDOW_DAYS]
    window = min(LIQUIDITY_WINDOW_DAYS, max(30.0, max(age(s) for s in usable)))
    adj = bias * drift
    return PriceEstimate(len(usable), eff, low * adj, mid * adj, high * adj, boots[int(.05 * 299)] * adj, boots[int(.95 * 299)] * adj,
                         1.4826 * mad / statistics.median(vals), slope, note, len(recent) * 30.0 / window,
                         min(age(s) for s in usable), method)


def months_to_sell(sales_per_month: float, competing_listings: int = 0) -> float:
    """Queue model: sales clear the listings ahead of you, then yours. Observed rate only, so a guess for thin cards."""
    if sales_per_month <= 0:
        return float("inf")
    return (competing_listings + 1) / sales_per_month


@dataclass(frozen=True)
class RiskView:
    expected_profit: float
    p_loss: float
    p_target: float
    p10_profit: float
    cvar10_profit: float
    risk_adjusted_profit: float
    annualized_return: float | None
    months_held: float
    max_buy: Decimal | None
    max_buy_confidence: float


def risk_view(sales: list[Sale], est: PriceEstimate, ask: Decimal, acquisition: AcquisitionCosts, exit_costs: ExitCosts, *,
              target_return: Decimal, capital_cost_monthly: Decimal, competing_listings: int = 0,
              confidence: float = 0.8) -> RiskView:
    """Resale drawn from the sales themselves, weighted by trust, scaled to the estimate. Holding time is charged as capital."""
    months = min(months_to_sell(est.sales_per_month, competing_listings), 36.0)
    cost = float(acquisition.all_in(ask))
    # Recentre on the estimate so learned bias and trend carry into every draw.
    base = statistics.median(s.price for s in sales) or 1.0
    scale = est.mid / base
    seed = int(hashlib.sha256(f"{est.mid}{ask}{len(sales)}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    draws = rng.choices([s.price * scale for s in sales], [s.trust for s in sales], k=N_SIMS)
    # Empirical draws understate tail risk on few sales: widen with the bootstrap error.
    sd = max(0.0, (est.ci_high - est.ci_low) / 3.3 / max(est.mid, 1.0))
    carry = cost * float(capital_cost_monthly) * months
    profits = sorted(float(exit_costs.net(Decimal(str(round(d * rng.lognormvariate(0, sd), 2))))) - cost - carry for d in draws)
    mean = statistics.fmean(profits)
    k = max(1, N_SIMS // 10)
    p10, cvar = profits[k - 1], statistics.fmean(profits[:k])
    target = cost * float(target_return)
    ret = (mean / cost) if cost > 0 else None
    # Simple rate: compounding a two-week flip twenty-six times a year assumes a next deal that may not exist.
    ann = ret * 12 / max(months, 1 / 12) if ret is not None else None
    q_price = _wq([(s.price * scale, s.trust) for s in sales], 1 - confidence)
    ceiling = buy_ceiling(CeilingInputs(Decimal(str(round(q_price, 2))), acquisition, exit_costs, target_return,
                                        Decimal(str(round(months, 2))), capital_cost_monthly)).buy_ceiling
    return RiskView(mean, sum(p < 0 for p in profits) / N_SIMS, sum(p >= target for p in profits) / N_SIMS, p10, cvar,
                    0.5 * mean + 0.5 * cvar, ann, months, ceiling, confidence)
