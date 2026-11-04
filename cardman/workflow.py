"""Observe -> Spectate -> Qualified -> human YES/WAIT/NO (CLAUDE.md section 6).

Qualified is a set of hard gates; nothing here can buy or bid.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from .identity import IdentityResult
from .models import IdentityStatus

WORKFLOW_VERSION = "workflow-0.1.0"


class Stage(str, Enum):
    OBSERVE = "OBSERVE"
    SPECTATE = "SPECTATE"
    QUALIFIED = "QUALIFIED"


class Action(str, Enum):
    YES = "YES"      # authorizes a next human action, never an autonomous purchase
    WAIT = "WAIT"
    NO = "NO"


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str


@dataclass
class DealEvaluation:
    listing_ref: str
    stage: Stage
    gates: list[Gate]
    ask_all_in: Decimal | None
    buy_ceiling: Decimal | None
    snapshot_id: str
    evaluated_at: datetime
    expires_at: datetime
    recheck_at: datetime
    reasons: list[str] = field(default_factory=list)


def snapshot_id(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def evaluate(*, listing_ref: str, identity: IdentityResult, n_comps: int, min_comps: int,
             ask_all_in: Decimal | None, ceiling: Decimal | None, ask_price: Decimal | None,
             listing_last_seen: datetime | None, now: datetime,
             authenticity_ok: bool | None, freshness: timedelta = timedelta(hours=6),
             snapshot_payload: dict | None = None) -> DealEvaluation:
    gates = [
        Gate("identity", identity.status == IdentityStatus.RESOLVED,
             f"{identity.status.value}: {'; '.join(identity.blocking_reasons) or 'ok'}"),
        Gate("authenticity", authenticity_ok is True,
             "not reviewed" if authenticity_ok is None else ("ok" if authenticity_ok else "failed")),
        Gate("comps", n_comps >= min_comps, f"{n_comps} verifiable sales (need {min_comps})"),
        Gate("ceiling", ceiling is not None and ask_price is not None and ask_price <= ceiling,
             f"ask {ask_price} vs ceiling {ceiling}"),
        Gate("fresh_availability", listing_last_seen is not None and now - listing_last_seen <= freshness,
             f"last seen {listing_last_seen}"),
    ]
    if all(g.passed for g in gates):
        stage = Stage.QUALIFIED
    elif identity.status in (IdentityStatus.CANDIDATES, IdentityStatus.UNRESOLVED) or n_comps < min_comps:
        stage = Stage.SPECTATE
    else:
        stage = Stage.OBSERVE
    sid = snapshot_id(snapshot_payload or {"listing": listing_ref, "gates": [asdict(g) for g in gates]})
    return DealEvaluation(listing_ref, stage, gates, ask_all_in, ceiling, sid, now,
                          expires_at=now + freshness, recheck_at=now + freshness / 2,
                          reasons=[g.name + ": " + g.detail for g in gates if not g.passed])


@dataclass(frozen=True)
class Decision:
    deal_snapshot_id: str
    action: Action
    rationale: str
    timestamp: datetime
    wait_trigger: str | None = None

    def __post_init__(self):
        if self.action == Action.WAIT and not self.wait_trigger:
            raise ValueError("WAIT needs a measurable re-evaluation trigger")


class AlertLog:
    """One alert per immutable opportunity (listing + snapshot)."""

    def __init__(self):
        self.sent: dict[str, DealEvaluation] = {}

    def maybe_alert(self, ev: DealEvaluation, now: datetime) -> bool:
        if ev.stage != Stage.QUALIFIED or now >= ev.expires_at:
            return False
        key = f"{ev.listing_ref}:{ev.snapshot_id}"
        if key in self.sent:
            return False
        self.sent[key] = ev
        return True

    def expired(self, now: datetime) -> list[str]:
        return [k for k, e in self.sent.items() if now >= e.expires_at]
