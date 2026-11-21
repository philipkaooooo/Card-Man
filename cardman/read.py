"""A mark for one printing from sales, asks and indexes already in hand.

A deep recent window uses the median of those sales, even when the month before was higher.
A thin falling window uses the lowest fresh sale. A live exact ask under the mark
replaces it. An index more than 12% above the recent sales is reported and never becomes the mark.
One source with a single sale cannot cancel a source that has a real run of them.
A listing count is only rows that are this printing. Mixed rows are kept on the page and not added.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import ceil

Q = Decimal("0.01")
DAY = timedelta(days=1)


@dataclass(frozen=True)
class Point:
    when: datetime
    amount: Decimal
    source: str
    kind: str          # sold | ask | index | listing | unavailable
    exact: bool = True
    note: str = ""


@dataclass(frozen=True)
class Held:
    source: str
    note: str
    listings: int | None = None


@dataclass(frozen=True)
class Read:
    as_of: datetime
    flags: tuple[str, ...]
    mark: Decimal | None
    stress: Decimal | None
    cheapest_ask: Decimal | None
    listings: int
    held: tuple[Held, ...]
    unavailable: tuple[str, ...]
    why: tuple[str, ...]


def _money(n: Decimal) -> Decimal:
    return n.quantize(Q, rounding=ROUND_HALF_UP)


def _median(vals: list[Decimal]) -> Decimal:
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return _money((s[mid - 1] + s[mid]) / 2)


def _rank(vals: list[Decimal], p: Decimal) -> Decimal:
    s = sorted(vals)
    idx = max(0, ceil(float(p) * len(s)) - 1)
    return s[idx]


def _dispersion(vals: list[Decimal]) -> Decimal:
    if len(vals) < 4:
        return Decimal(0)
    mid = _median(vals)
    if mid <= 0:
        return Decimal("Infinity")
    return (_rank(vals, Decimal("0.75")) - _rank(vals, Decimal("0.25"))) / mid


def _dedupe(solds: list[Point]) -> list[Point]:
    kept: list[Point] = []
    for row in solds:
        twin = next((prev for prev in kept
                     if prev.source != row.source
                     and abs(prev.when - row.when) <= DAY
                     and abs(prev.amount - row.amount) / max(prev.amount, row.amount) <= Decimal("0.001")), None)
        if twin is None:
            kept.append(row)
    return kept


def read_points(points: list[Point], as_of: datetime) -> Read:
    exact = [p for p in points if p.exact and p.kind != "unavailable"]
    held = tuple(Held(p.source, p.note or "not this printing", int(p.amount) if p.kind == "listing" else None)
                 for p in points if not p.exact and p.kind != "unavailable")
    unavailable = tuple(p.source for p in points if p.kind == "unavailable")

    solds = _dedupe(sorted((p for p in exact if p.kind == "sold" and p.amount > 0 and p.when <= as_of),
                           key=lambda p: p.when))
    asks = [p for p in exact if p.kind == "ask" and p.amount > 0 and p.when <= as_of]
    indexes = [p for p in exact if p.kind == "index" and p.amount > 0]
    counts = [p for p in exact if p.kind == "listing" and p.amount > 0]

    recent = [p for p in solds if as_of - p.when <= 14 * DAY]
    prior = [p for p in solds if 14 * DAY < as_of - p.when <= 45 * DAY]
    in45 = [p for p in solds if as_of - p.when <= 45 * DAY]

    flags: list[str] = []
    why: list[str] = []

    if len(recent) < 3:
        flags.append("thin")
        why.append(f"{len(recent)} exact sales in 14 days. A price is not named under 3.")

    falling = False
    if len(prior) >= 2 and len(recent) >= 1:
        recent_med = _median([p.amount for p in recent])
        prior_med = _median([p.amount for p in prior])
        if recent_med < prior_med * Decimal("0.85"):
            falling = True
            flags.append("falling")
            why.append(f"Recent median {recent_med} is under 85% of the prior median {prior_med}. Older sales are off the mark.")

    if solds and not in45:
        flags.append("stale")
        why.append("No exact sale in 45 days. Older sales do not set a live price.")

    window = recent if (falling or len(recent) >= 4) else in45
    if _dispersion([p.amount for p in window]) > Decimal("0.45"):
        flags.append("wide")
        why.append("The sale spread is too wide to treat as one price.")

    by_source: dict[str, list[Decimal]] = {}
    for p in recent:
        by_source.setdefault(p.source, []).append(p.amount)
    heavy = [(src, _median(vals)) for src, vals in by_source.items() if len(vals) >= 3]
    if len(heavy) >= 2:
        hi = max(m for _, m in heavy)
        lo = min(m for _, m in heavy)
        if hi > 0 and (hi - lo) / hi > Decimal("0.1"):
            flags.append("contested")
            bits = " and ".join(f"{src} {med}" for src, med in heavy)
            why.append(f"{bits} differ by more than 10%. No single price.")

    anchor = _median([p.amount for p in recent]) if recent else (solds[-1].amount if solds else None)
    if anchor is not None:
        for p in indexes:
            lag = (p.amount - anchor) / anchor
            if lag > Decimal("0.12"):
                flags.append("index-lag")
                why.append(f"{p.source} index {p.amount} sits {(lag * 100).quantize(Decimal('0.1'))}% above recent sales. Discarded.")

    cheapest = min((p.amount for p in asks), default=None)
    if cheapest is not None and anchor is not None and cheapest < anchor:
        flags.append("ask-undercut")
        why.append(f"Cheapest exact ask {cheapest} is under the recent median {anchor}. The ask caps the mark.")

    contested = "contested" in flags
    wide = "wide" in flags
    thin = "thin" in flags
    stale = "stale" in flags
    mark: Decimal | None = None
    recent_prices = [p.amount for p in recent]
    thick = len(recent) >= 8 and _dispersion(recent_prices) <= Decimal("0.45")
    if not contested and not wide and not stale:
        if not thin and thick:
            mark = _median(recent_prices)
            why.append(f"{len(recent)} sales in 14 days. The mark is their median, {mark}.")
            if falling:
                why.append("The level is down from the month before. The recent sample is deep enough that the worst print does not set the mark.")
        elif not thin and falling and len(recent) >= 3:
            last3 = sorted(recent, key=lambda p: p.when, reverse=True)[:3]
            mark = min(p.amount for p in last3)
            why.append(f"Few fresh sales and the level is falling. The mark is the low of the last 3, {mark}.")
        elif not thin and not falling and len(in45) >= 5:
            mark = _rank([p.amount for p in in45], Decimal("0.2"))
            why.append(f"Settled, so the mark is the low fifth of sales in 45 days, {mark}.")
        elif thin and falling and recent:
            mark = min(p.amount for p in recent)
            why.append(f"Only {len(recent)} sale(s) in 14 days, under 85% of the month before. The mark is the lowest of them, {mark}.")
        if mark is not None and cheapest is not None and cheapest < mark:
            why.append(f"Ask {cheapest} replaced {mark}.")
            mark = cheapest
        if mark is not None:
            mark = _money(mark)

    stress = None
    if len(recent) >= 3:
        last3 = sorted(recent, key=lambda p: p.when, reverse=True)[:3]
        stress = _money(min(p.amount for p in last3))
    elif recent:
        stress = _money(min(p.amount for p in recent))

    listings = int(sum(p.amount for p in counts))
    if listings:
        detail = " ".join(f"{p.source} {int(p.amount)} ({p.note})" for p in counts)
        why.append(f"{listings} exact listings. {detail} Mixed and missing feeds are not added.")
    elif asks and not counts:
        listings = len(asks)
        why.append(f"{listings} exact asks on the book. Mixed rows are not added.")

    # flags unique, index-lag may have been appended once per index
    seen: list[str] = []
    for f in flags:
        if f not in seen:
            seen.append(f)
    return Read(as_of, tuple(seen), mark, stress, cheapest, listings, held, unavailable, tuple(why))


REFUSED = ("contested", "wide", "stale")


def refused_reason(rd: Read) -> str | None:
    """A read that must not fall back to an older window's quantile."""
    if "contested" in rd.flags:
        return "sources with a real run of sales disagree by more than 10%"
    if "wide" in rd.flags:
        return "the sale spread is too wide to be one price"
    if "stale" in rd.flags:
        return "no exact sale in 45 days"
    return None


def apply_read(rd: Read, scenarios):
    """The mark replaces the conservative exit. A refused read clears the exit instead of keeping the old one."""
    from dataclasses import replace
    reason = refused_reason(rd)
    if reason:
        return replace(scenarios, conservative=None, base=None, upside=None, method="read", abstain_reason=reason)
    if rd.mark is not None:
        return replace(scenarios, conservative=rd.mark, method="read")
    return scenarios
