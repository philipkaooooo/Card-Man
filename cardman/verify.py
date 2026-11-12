"""Second-pass verification of sale evidence: duplicates dedup() cannot see, and sales that may not be real.

Nothing is rewritten. Each observation gets a trust score and named flags. Only flags in EXCLUDING remove a sale from
comparables, and the reason is reported next to the record. The original stays in the store.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .models import COMP_ELIGIBLE, EvidenceType, MarketObservation

CROSS_POST_DUP = "CROSS_POST_DUP"       # same sale reported by two sources without shared lineage
OUTLIER_HIGH = "OUTLIER_HIGH"
OUTLIER_LOW = "OUTLIER_LOW"
SELLER_CHURN = "SELLER_CHURN"           # one seller keeps "selling" the card at rising prices
RELISTED_AFTER_SALE = "RELISTED_AFTER_SALE"   # possible non-payment
CERT_FLIP = "CERT_FLIP"                 # same slab resold fast at a big jump
SHIPPING_GAMED = "SHIPPING_GAMED"       # price moved into shipping
UNPROVEN_SETTLEMENT = "UNPROVEN_SETTLEMENT"

EXCLUDING = {CROSS_POST_DUP, RELISTED_AFTER_SALE, SHIPPING_GAMED}
PENALTY = {OUTLIER_HIGH: 0.5, OUTLIER_LOW: 0.5, SELLER_CHURN: 0.5, CERT_FLIP: 0.7, UNPROVEN_SETTLEMENT: 0.85}
BASE_TRUST = {EvidenceType.INDEPENDENT_TRANSACTION: 1.0, EvidenceType.ACCEPTED_OFFER: 0.9, EvidenceType.MARKETPLACE_MARKED_SOLD: 0.8}


@dataclass
class Assessment:
    obs_id: str
    trust: float
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> bool:
        return any(f in EXCLUDING for f in self.flags)


def _modified_z(values: list[float]) -> list[float]:
    logs = [math.log(v) for v in values]
    med = statistics.median(logs)
    mad = statistics.median(abs(x - med) for x in logs)
    if mad == 0:
        return [0.0] * len(values)
    return [0.6745 * (x - med) / mad for x in logs]


def assess(observations: list[MarketObservation], as_of: datetime) -> dict[str, Assessment]:
    """Observations for one printing and grade. Uses only what was known at `as_of`."""
    known = [o for o in observations if o.capture_time <= as_of and o.event_time <= as_of]
    sales = sorted((o for o in known if o.evidence_type in COMP_ELIGIBLE and o.status == "VALID" and o.original_amount > 0),
                   key=lambda o: (-COMP_ELIGIBLE[o.evidence_type], o.event_time))
    out = {o.id: Assessment(o.id, BASE_TRUST[o.evidence_type]) for o in sales}

    def flag(o, name, note):
        a = out[o.id]
        a.flags.append(name)
        a.notes.append(note)
        a.trust *= PENALTY.get(name, 0.0)

    for o in sales:
        if o.evidence_type == EvidenceType.MARKETPLACE_MARKED_SOLD:
            flag(o, UNPROVEN_SETTLEMENT, "A sold badge does not prove the buyer paid.")
        if o.shipping_amount is not None and o.shipping_amount > o.original_amount * 2:
            flag(o, SHIPPING_GAMED, f"Shipping {o.shipping_amount} dwarfs the price {o.original_amount}: the sale price is not the real price.")

    # Sales kept so far, strongest first: a weaker record matching one is the duplicate.
    # One-to-one: a kept sale absorbs at most one record from each other source, so two real sales at the same price
    # on one site are not both swallowed by a single sale seen on another.
    kept: list[MarketObservation] = []
    absorbed: set[tuple[str, str]] = set()
    for o in sales:
        twin = next((k for k in kept if k.source != o.source and (k.id, o.source) not in absorbed and k.currency == o.currency
                     and abs(k.original_amount - o.original_amount) <= k.original_amount * Decimal('0.01')
                     and abs(k.event_time - o.event_time) <= timedelta(days=2)
                     and (k.seller_ref == o.seller_ref or not k.seller_ref or not o.seller_ref)), None)
        if twin:
            absorbed.add((twin.id, o.source))
            flag(o, CROSS_POST_DUP, f"Same price within 2 days as {twin.id} on {twin.source}.")
        else:
            kept.append(o)

    by_cur: dict[str, list[MarketObservation]] = {}
    for o in kept:
        by_cur.setdefault(o.currency, []).append(o)
    for group in by_cur.values():
        if len(group) >= 5:
            for o, z in zip(group, _modified_z([float(x.original_amount) for x in group])):
                if z > 3.5:
                    flag(o, OUTLIER_HIGH, f"Far above the other sales (z={z:.1f}).")
                elif z < -3.5:
                    flag(o, OUTLIER_LOW, f"Far below the other sales (z={z:.1f}): damage, misidentified or non-arm's-length.")

    by_seller: dict[str, list[MarketObservation]] = {}
    for o in kept:
        if o.seller_ref:
            by_seller.setdefault(o.seller_ref, []).append(o)
    for seller, group in by_seller.items():
        group.sort(key=lambda o: o.event_time)
        for i in range(len(group) - 2):
            w = group[i:i + 3]
            if (w[-1].event_time - w[0].event_time <= timedelta(days=30)
                    and all(a.original_amount < b.original_amount for a, b in zip(w, w[1:]))):
                for o in w:
                    if SELLER_CHURN not in out[o.id].flags:
                        flag(o, SELLER_CHURN, "One seller shows 3 rising sales inside 30 days: could be price painting.")

    for o in kept:
        later = [a for a in known if a.evidence_type in (EvidenceType.ACTIVE_ASKING, EvidenceType.OBSERVED_BID)
                 and a.seller_ref and a.seller_ref == o.seller_ref and timedelta(0) < a.event_time - o.event_time <= timedelta(days=14)
                 and (a.cert_number == o.cert_number if o.cert_number else o.grade_company is None and not o.quantity > 1
                      and a.listing_id == o.listing_id)]
        if later:
            flag(o, RELISTED_AFTER_SALE, "Same seller listed it again within 14 days: the sale may have fallen through.")

    by_cert: dict[str, list[MarketObservation]] = {}
    for o in kept:
        if o.cert_number:
            by_cert.setdefault(o.cert_number, []).append(o)
    for group in by_cert.values():
        group.sort(key=lambda o: o.event_time)
        for a, b in zip(group, group[1:]):
            if b.currency == a.currency and b.event_time - a.event_time <= timedelta(days=14) and b.original_amount > a.original_amount * Decimal('1.3'):
                flag(b, CERT_FLIP, f"Same slab sold for {a.original_amount} under two weeks earlier and {b.original_amount} here: one flip, not a market move.")
    return out


def filter_comps(comps: list[MarketObservation], pool: list[MarketObservation], as_of: datetime
                 ) -> tuple[list[MarketObservation], list[tuple[str, str]]]:
    verdicts = assess(pool, as_of)
    kept, rejected = [], []
    for c in comps:
        v = verdicts.get(c.id)
        if v and v.excluded:
            rejected.append((c.id, "verification: " + "; ".join(v.notes)))
        else:
            kept.append(c)
    return kept, rejected
