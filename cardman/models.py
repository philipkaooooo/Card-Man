"""Core entities (CLAUDE.md section 8). Money is Decimal, times are tz-aware."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

SCHEMA_VERSION = "0.2.0"


class Region(str, Enum):
    JP_OCG = "JP_OCG"
    EN_TCG = "EN_TCG"
    ASIAN_EN_OCG_QUARANTINE = "ASIAN_EN_OCG_QUARANTINE"


class GameFormat(str, Enum):
    DUEL_MONSTERS = "DUEL_MONSTERS"
    RUSH_DUEL = "RUSH_DUEL"


class IdentityStatus(str, Enum):
    RESOLVED = "RESOLVED"          # exactly one printing, all required evidence agrees
    CANDIDATES = "CANDIDATES"      # >1 plausible printing, abstain
    CONFLICT = "CONFLICT"          # evidence contradicts itself (hard block)
    QUARANTINED = "QUARANTINED"    # Asian-English / Rush Duel etc: not tradable as EN TCG
    UNRESOLVED = "UNRESOLVED"      # nothing matched


class EvidenceType(str, Enum):
    ACTIVE_ASKING = "ACTIVE_ASKING"
    OBSERVED_BID = "OBSERVED_BID"
    AUCTION_ENDED_UNSOLD = "AUCTION_ENDED_UNSOLD"
    MARKETPLACE_MARKED_SOLD = "MARKETPLACE_MARKED_SOLD"
    ACCEPTED_OFFER = "ACCEPTED_OFFER"
    INDEPENDENT_TRANSACTION = "INDEPENDENT_TRANSACTION"
    AGGREGATE_ESTIMATE = "AGGREGATE_ESTIMATE"
    SELLER_CLAIM = "SELLER_CLAIM"


# Evidence classes that may be used as sold comparables, ranked by strength.
COMP_ELIGIBLE = {
    EvidenceType.INDEPENDENT_TRANSACTION: 3,
    EvidenceType.ACCEPTED_OFFER: 2,
    EvidenceType.MARKETPLACE_MARKED_SOLD: 1,
}


def _require_tz(ts: datetime, name: str) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be Decimal")
        if len(self.currency) != 3:
            raise ValueError("currency must be ISO-4217")

    def __add__(self, other: "Money") -> "Money":
        if other.currency != self.currency:
            raise ValueError(f"no silent currency coercion: {self.currency} + {other.currency}")
        return Money(self.amount + other.amount, self.currency)


@dataclass(frozen=True)
class CardFamily:
    id: str
    names: dict[str, str]                 # {"en": ..., "ja": ...}
    konami_id: int | None = None
    passcode: str | None = None           # NOT a printing id
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtworkVariant:
    id: str
    family_id: str
    visual_key: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Printing:
    id: str
    family_id: str
    artwork_id: str
    region: Region
    language: str
    product_id: str
    rarity: str
    set_code: str | None = None           # vintage JP may have none
    edition: str | None = None            # "1st", "Unlimited", "Limited", None
    release_date: str | None = None
    promo_channel: str | None = None
    game_format: GameFormat = GameFormat.DUEL_MONSTERS
    variant_evidence: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    product_name: str | None = None       # human product/promo name as the source spells it
    # "documented" | "incomplete" (e.g. rarity missing in source). Incomplete printings never RESOLVE.
    identity_status: str = "documented"
    # True only once cross-checked against a primary identity source (Konami DB / YGOResources).
    # Secondary compiled sources (YAML Yugi, YGOPRODeck) leave this False.
    verified_against_source: bool = False


@dataclass(frozen=True)
class Specimen:
    id: str
    printing_id: str | None
    grade_company: str | None = None
    grade: str | None = None
    cert_number: str | None = None        # cert is NOT the set code and NOT authenticity proof
    raw_condition: str | None = None
    photos: tuple[str, ...] = ()
    authenticity_status: str = "UNVERIFIED"


@dataclass(frozen=True)
class MarketObservation:
    """Immutable evidence record. Original fields are never rewritten."""
    id: str
    evidence_type: EvidenceType
    source: str
    original_amount: Decimal
    currency: str
    event_time: datetime
    capture_time: datetime
    source_url: str
    printing_candidates: tuple[str, ...]
    listing_id: str | None = None
    source_event_id: str | None = None
    seller_ref: str | None = None
    shipping_amount: Decimal | None = None
    quantity: int = 1
    is_lot: bool = False
    grade_company: str | None = None
    grade: str | None = None
    cert_number: str | None = None
    observed_condition: str | None = None
    raw_ref: str | None = None
    upstream_lineage: tuple[str, ...] = ()   # e.g. ("ebay:123",) when syndicated
    synthetic_fixture: bool = False          # never displayed as market evidence
    status: str = "VALID"
    status_reason: str | None = None

    def __post_init__(self):
        _require_tz(self.event_time, "event_time")
        _require_tz(self.capture_time, "capture_time")
        if not isinstance(self.original_amount, Decimal):
            raise TypeError("original_amount must be Decimal")


@dataclass(frozen=True)
class SourceCapability:
    source: str
    capability: str
    access_mode: str
    supported: bool
    tested: bool
    authenticated: bool
    permitted: str             # "yes" | "no" | "unknown" | "needs_terms_review"
    available: bool
    tested_at: datetime | None
    last_success: datetime | None
    failure_reason: str | None = None
    rate_limit: str | None = None
    coverage: str | None = None
    cost: str | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)
