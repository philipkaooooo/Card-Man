"""Timestamped FX. No silent coercion, no stale rates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


class StaleRateError(ValueError):
    pass


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    mid: Decimal
    as_of: datetime
    source: str
    spread_pct: Decimal = Decimal("0")   # actual conversion spread vs mid, when known


class FxTable:
    def __init__(self, rates: list[FxRate], max_age: timedelta = timedelta(days=1)):
        self.rates = rates
        self.max_age = max_age

    def rate(self, base: str, quote: str, at: datetime) -> FxRate:
        if base == quote:
            return FxRate(base, quote, Decimal(1), at, "identity")
        cands = [r for r in self.rates if r.base == base and r.quote == quote and r.as_of <= at]
        if not cands:
            # The inverse pair is the same fact (USD/TWD 32 is TWD/USD 1/32), with the same timestamp rules.
            inv = [r for r in self.rates if r.base == quote and r.quote == base and r.as_of <= at]
            cands = [FxRate(base, quote, Decimal(1) / r.mid, r.as_of, r.source + " (inverse)", r.spread_pct)
                     for r in inv]
        if not cands:
            raise StaleRateError(f"no {base}/{quote} rate at or before {at.isoformat()}")
        r = max(cands, key=lambda r: r.as_of)
        if at - r.as_of > self.max_age:
            raise StaleRateError(f"{base}/{quote} rate from {r.as_of.isoformat()} is stale for {at.isoformat()}")
        return r

    def convert(self, amount: Decimal, base: str, quote: str, at: datetime, *, apply_spread: bool = False) -> Decimal:
        r = self.rate(base, quote, at)
        v = amount * r.mid
        if apply_spread:
            v = v * (1 + r.spread_pct)
        return v
