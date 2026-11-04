"""Evidence store, dedup and comparable selection (CLAUDE.md sections 3-4)."""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .models import COMP_ELIGIBLE, EvidenceType, MarketObservation

MAX_COMPS = 10


def lineage_key(o: MarketObservation) -> str:
    """One real sale syndicated through several feeds shares the upstream id."""
    if o.upstream_lineage:
        return "lineage:" + sorted(o.upstream_lineage)[0]
    if o.source_event_id:
        return f"{o.source}:{o.source_event_id}"
    if o.listing_id:
        # Same listing id + same event time = same event; a relist has a new time/id.
        return f"{o.source}:{o.listing_id}:{o.event_time.isoformat()}"
    return "obs:" + o.id


def dedup(observations: list[MarketObservation]) -> tuple[list[MarketObservation], list[tuple[str, str]]]:
    """Keep the strongest record per lineage. Returns (kept, [(dropped_id, reason)]).

    Genuine separate sales of the same slab (same cert, different dates) are kept.
    """
    best: dict[str, MarketObservation] = {}
    dropped: list[tuple[str, str]] = []
    for o in observations:
        k = lineage_key(o)
        cur = best.get(k)
        if cur is None:
            best[k] = o
            continue
        rank = lambda x: (COMP_ELIGIBLE.get(x.evidence_type, 0), not x.upstream_lineage)
        keep, drop = (o, cur) if rank(o) > rank(cur) else (cur, o)
        best[k] = keep
        dropped.append((drop.id, f"duplicate of {keep.id} via {k}"))
    return list(best.values()), dropped


def select_comps(observations: list[MarketObservation], printing_id: str, *,
                 grade_company: str | None = None, grade: str | None = None,
                 raw_condition: str | None = None, as_of: datetime,
                 include_synthetic: bool = False) -> tuple[list[MarketObservation], list[tuple[str, str]]]:
    """Up to 10 most recent verifiable sales for one exact printing / grade / condition.

    Never substitutes active listings, bids, aggregates or claims for sales.
    """
    rejected: list[tuple[str, str]] = []
    pool, dups = dedup(observations)
    rejected += dups
    out = []
    for o in pool:
        reason = None
        if o.evidence_type not in COMP_ELIGIBLE:
            reason = f"{o.evidence_type.value} is not a sale"
        elif o.status != "VALID":
            reason = f"status {o.status}: {o.status_reason}"
        elif o.synthetic_fixture and not include_synthetic:
            reason = "synthetic fixture"
        elif o.printing_candidates != (printing_id,):
            reason = "identity not resolved to this exact printing"
        elif o.is_lot or o.quantity != 1:
            reason = "lot / multi-quantity, not a single"
        elif (o.grade_company, o.grade) != (grade_company, grade):
            reason = f"grade mismatch {o.grade_company} {o.grade}"
        elif grade_company is None and raw_condition and o.observed_condition != raw_condition:
            reason = f"condition mismatch {o.observed_condition}"
        elif o.event_time > as_of:
            reason = "after decision time (leakage)"
        if reason:
            rejected.append((o.id, reason))
        else:
            out.append(o)
    out.sort(key=lambda o: o.event_time, reverse=True)
    return out[:MAX_COMPS], rejected


class RawEvidenceStore:
    """Append-only raw payload store, content-addressed."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, source: str, payload: bytes, meta: dict) -> str:
        h = hashlib.sha256(payload).hexdigest()
        d = self.root / source
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{h}.bin"
        if not p.exists():
            p.write_bytes(payload)
            (d / f"{h}.meta.json").write_text(json.dumps(meta, default=str, ensure_ascii=False, indent=1))
        return f"{source}/{h}"


def import_manual_csv(path: Path, captured_at: datetime) -> list[MarketObservation]:
    """Manual-import fallback: user-provided rows with a source URL each.

    Required columns: id, evidence_type, source, amount, currency, event_time, source_url, printing_id
    Optional: listing_id, source_event_id, seller, shipping, grade_company, grade, condition, is_lot, upstream
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("source_url"):
                raise ValueError(f"row {r.get('id')}: source_url is required")
            et = datetime.fromisoformat(r["event_time"])
            rows.append(MarketObservation(
                id=r["id"], evidence_type=EvidenceType(r["evidence_type"]), source=r["source"],
                original_amount=Decimal(r["amount"]), currency=r["currency"].upper(),
                event_time=et, capture_time=captured_at, source_url=r["source_url"],
                printing_candidates=tuple(x for x in r["printing_id"].split("|") if x),
                listing_id=r.get("listing_id") or None, source_event_id=r.get("source_event_id") or None,
                seller_ref=r.get("seller") or None,
                shipping_amount=Decimal(r["shipping"]) if r.get("shipping") else None,
                grade_company=r.get("grade_company") or None, grade=r.get("grade") or None,
                observed_condition=r.get("condition") or None,
                is_lot=(r.get("is_lot", "").lower() == "true"),
                upstream_lineage=tuple(x for x in (r.get("upstream") or "").split("|") if x),
                raw_ref=f"manual_csv:{Path(path).name}",
                # Rows without a real web source are treated as fixtures, never as market evidence.
                synthetic_fixture=(r.get("synthetic", "").lower() == "true"
                                   or not r["source_url"].startswith(("http://", "https://"))),
            ))
    return rows
