"""One region of a dated post: a card, and the number written on that same region.

The number is what that account quoted. It is a seller claim. It is not a sale,
not a TCGplayer market price, and not an exit. A caption under a stack of cards
does not price any of them. A name with no code does not name a printing.

A quote is scored only after it is attached to one SKU
(product × variant × condition × language). The mixed market is the mirror
case: LOB-001's market covers ten SKUs, so a damaged card quoted at that
market is the account repeating the page, not the damaged book.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from .evidence import select_comps
from .identity import parse_set_code
from .models import EvidenceType, MarketObservation
from .tcgplayer import MIN_SALES

# Worldwide English of these sets is Unlimited only. Same rule as editions_for:
# a code whose suffix starts with EN loses 1st. LOB-EN001 is not a 1st.
_WW_EN_PREFIX = frozenset({"LOB", "MRD", "SRL", "PSV", "LON", "LOD", "DCR", "IOC"})
# Unsiffixed codes that also exist as Asian-English. No product id, no join.
_AE_COLLISION = frozenset({"LOB-001", "SDK-001", "LOB-005", "SDY-006", "MFC-000"})
# Printed code that is a different product. 21792 is LOB-001, not LOB-EN001.
_NOT_THIS_PRODUCT = {"LOB-EN001": frozenset({21792})}

_CONDITION = {
    "near mint": "Near Mint", "nm": "Near Mint",
    "lightly played": "Lightly Played", "lp": "Lightly Played",
    "moderately played": "Moderately Played", "mp": "Moderately Played",
    "heavily played": "Heavily Played", "hp": "Heavily Played",
    "damaged": "Damaged", "dmg": "Damaged",
}
_VARIANT = {
    "1st": "1st Edition", "1st edition": "1st Edition", "first edition": "1st Edition",
    "unlimited": "Unlimited", "unl": "Unlimited",
    "limited": "Limited",
    "holofoil": "Holofoil", "holo": "Holofoil",
    "reverse holofoil": "Reverse Holofoil", "reverse holo": "Reverse Holofoil",
    "normal": "Normal",
}
_LANGUAGE = {
    "en": "English", "english": "English",
    "ja": "Japanese", "jp": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "korean": "Korean",
    "de": "German", "german": "German",
    "fr": "French", "french": "French",
    "it": "Italian", "italian": "Italian",
    "es": "Spanish", "sp": "Spanish", "spanish": "Spanish",
    "pt": "Portuguese", "portuguese": "Portuguese",
    "zh-hant": "Chinese", "zh-hans": "Chinese", "chinese": "Chinese",
}
# Quote vs the exit. The mirror check is tighter, so a round number can match
# the page market without being called the book.
_AT_BOOK = Decimal("0.05")
_MIRROR = Decimal("0.02")


@dataclass(frozen=True)
class PostRegion:
    """One photo cell, or one line that was read from that cell. Not a stack."""
    source: str
    posted_at: datetime
    region: str
    amount: Decimal | None
    currency: str
    amount_in_region: bool
    printed_code: str | None = None
    caption_code: str | None = None
    language: str | None = None
    variant: str | None = None
    condition: str | None = None
    product_id: int | None = None
    grade_company: str | None = None
    grade: str | None = None
    is_multi: bool = False
    said_sold: bool = False
    source_url: str = ""
    synthetic: bool = False


@dataclass(frozen=True)
class Book:
    """The same SKU's cleared exit, and the product's mixed market beside it.

    `market` mixes conditions and variants. It is only here so a quote can be
    recognized as a copy of that number.
    """
    exit_low: Decimal | None
    sales: int
    market: Decimal | None = None
    prior_low: Decimal | None = None
    high_day: Decimal | None = None
    currency: str = "USD"


@dataclass(frozen=True)
class QuoteRead:
    status: str
    verdict: str
    card_key: str | None
    why: str
    evidence_type: str | None

    @property
    def attached(self) -> bool:
        return self.status == "ATTACHED" and self.card_key is not None


def _code(raw: str | None) -> str | None:
    parsed = parse_set_code(raw)
    if not parsed or parsed["kind"] != "set_code":
        return None
    return parsed["code"]


def _lookup(table: dict[str, str], raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    return table.get(text.casefold())


def _variant(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    hit = _VARIANT.get(text.casefold())
    if hit:
        return hit
    # "Unlimited Holofoil" is a different printing from Unlimited and from Holofoil.
    return text


def _close(quote: Decimal, other: Decimal | None, tol: Decimal) -> bool:
    if other is None or other <= 0:
        return False
    return abs(quote - other) / other <= tol


def _same_dollar(a: Decimal, b: Decimal) -> bool:
    dollar = Decimal("1")
    return a.quantize(dollar, rounding=ROUND_HALF_EVEN) == b.quantize(dollar, rounding=ROUND_HALF_EVEN)


def _why(text: str, region: PostRegion) -> str:
    head = "A caption that says sold is not a receipt. " if region.said_sold and region.amount is not None else ""
    tail = " Not a sale." if region.amount is not None else ""
    return head + text + tail


def read_region(region: PostRegion, book: Book | None = None) -> QuoteRead:
    """Attach the quote to one SKU, or refuse. Then say what the number is.

    Refusal wins over a flattering gap. There is no profit in this result.
    """
    evidence = EvidenceType.SELLER_CLAIM.value if region.amount is not None else None
    if region.posted_at.tzinfo is None or region.posted_at.utcoffset() is None:
        raise ValueError("posted_at must be timezone-aware")
    if region.amount is not None and region.amount <= 0:
        return QuoteRead("UNRESOLVED", "", None, _why("The amount is not a price.", region), evidence)

    if region.is_multi or not region.amount_in_region:
        why = "A stack of cards is not one card." if region.is_multi else "The number is not in this card's photo."
        return QuoteRead("DETACHED", "", None, _why(why, region), evidence)
    if region.grade_company or region.grade:
        return QuoteRead("SLAB", "", None, _why(
            "A slab is not a raw condition, and a cert number is not a set code.", region), evidence)

    printed = _code(region.printed_code)
    caption = _code(region.caption_code)
    if printed and caption and printed != caption:
        return QuoteRead("CONFLICT", "", None, _why(
            f"The card shows {printed} and the caption says {caption}.", region), evidence)
    code = printed
    if region.printed_code and not code and region.product_id is None:
        return QuoteRead("UNRESOLVED", "", None, _why(
            f"{region.printed_code} is not a set code this resolver knows.", region), evidence)

    variant = _variant(region.variant)
    condition = _lookup(_CONDITION, region.condition)
    language = _lookup(_LANGUAGE, region.language)

    if code and variant == "1st Edition":
        prefix, _, suffix = code.partition("-")
        if prefix in _WW_EN_PREFIX and suffix.startswith("EN"):
            return QuoteRead("CONFLICT", "", None, _why(
                f"{code} is a worldwide English code. That run is Unlimited, so a 1st stamp contradicts it.",
                region), evidence)
    if code and region.product_id is not None and region.product_id in _NOT_THIS_PRODUCT.get(code, ()):
        return QuoteRead("CONFLICT", "", None, _why(
            f"{code} is not product {region.product_id}.", region), evidence)
    if code in _AE_COLLISION and region.product_id is None:
        return QuoteRead("QUARANTINED", "", None, _why(
            f"{code} is also an Asian-English code. No product id, so it is not joined to English.",
            region), evidence)

    missing = [name for name, value in (("variant", variant), ("condition", condition), ("language", language)) if not value]
    if missing:
        return QuoteRead("UNRESOLVED", "", None, _why(
            "Missing " + ", ".join(missing) + ". A price needs a variant, a condition and a language.",
            region), evidence)
    if region.product_id is None and not code:
        return QuoteRead("UNRESOLVED", "", None, _why(
            "No printed code and no product id. A name is not a printing.", region), evidence)

    if region.product_id is not None:
        key = f"tcg:{region.product_id}|{variant}|{condition}|{language}"
        status = "ATTACHED"
    else:
        key = f"code:{code}|{variant}|{condition}|{language}"
        status = "NAMED"

    if region.amount is None:
        return QuoteRead(status, "", key, "No number in this region. The card is not priced.", None)
    if status != "ATTACHED" or book is None:
        why = ("Named, and not joined to a product, so there is no book to score it against."
               if status == "NAMED" else "No same-SKU book was passed in.")
        verdict = "" if status == "NAMED" else "NO_BOOK"
        return QuoteRead(status, verdict, key, _why(why, region), evidence)
    if book.currency != region.currency:
        return QuoteRead(status, "CURRENCY", key, _why(
            f"Quote is {region.currency} and the book is {book.currency}. No conversion is applied here.",
            region), evidence)
    if book.exit_low is None or book.sales < MIN_SALES:
        return QuoteRead(status, "NO_BOOK", key, _why(
            f"{book.sales} usable sale(s) of this SKU. Not a book.", region), evidence)

    quote = region.amount
    if _close(quote, book.exit_low, _AT_BOOK):
        return QuoteRead(status, "AT_BOOK", key, _why(
            f"Quote {quote} is within 5% of this SKU's exit {book.exit_low}.", region), evidence)
    market_is_book = book.market is not None and _close(book.market, book.exit_low, _AT_BOOK)
    if book.market is not None and not market_is_book and (
            _close(quote, book.market, _MIRROR) or _same_dollar(quote, book.market)):
        return QuoteRead(status, "MIRROR", key, _why(
            f"Quote {quote} repeats the mixed market {book.market}, not this SKU's exit {book.exit_low}.",
            region), evidence)
    if (book.high_day is not None and not _close(book.high_day, book.exit_low, _AT_BOOK)
            and (_close(quote, book.high_day, _MIRROR) or quote == book.high_day)):
        return QuoteRead(status, "HIGH_DAY", key, _why(
            f"Quote {quote} matches a high day of {book.high_day}. The exit is {book.exit_low}.",
            region), evidence)
    if (book.prior_low is not None and not _close(book.prior_low, book.exit_low, _AT_BOOK)
            and _close(quote, book.prior_low, _MIRROR)):
        return QuoteRead(status, "LAG", key, _why(
            f"Quote {quote} matches the older half {book.prior_low}. The exit is {book.exit_low}.",
            region), evidence)
    gap = quote - book.exit_low
    side = f"{gap} above" if gap > 0 else f"{-gap} under"
    return QuoteRead(status, "OFF", key, _why(
        f"Quote {quote} is {side} this SKU's exit {book.exit_low}. Not a profit and not a buy.",
        region), evidence)


def to_observation(region: PostRegion, result: QuoteRead, *, capture_time: datetime) -> MarketObservation | None:
    """A claim stored against its key. Never a sale type, so select_comps refuses it."""
    if region.amount is None or result.evidence_type != EvidenceType.SELLER_CLAIM.value:
        return None
    if capture_time.tzinfo is None:
        raise ValueError("capture_time must be timezone-aware")
    key = result.card_key or ""
    raw = "|".join([region.source, region.region, region.posted_at.isoformat(),
                    str(region.amount), region.currency, key, result.status, result.verdict])
    obs_id = "post:" + hashlib.sha256(raw.encode()).hexdigest()[:20]
    return MarketObservation(
        id=obs_id, evidence_type=EvidenceType.SELLER_CLAIM, source=region.source,
        original_amount=region.amount, currency=region.currency,
        event_time=region.posted_at, capture_time=capture_time,
        source_url=region.source_url, printing_candidates=(key,) if key else (),
        listing_id=region.region,
        source_event_id=f"{region.source}:{region.region}:{region.posted_at.date().isoformat()}",
        seller_ref=region.source, observed_condition=region.condition, raw_ref=result.why,
        synthetic_fixture=region.synthetic,
    )


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def region_from_json(doc: dict) -> tuple[PostRegion, Book | None]:
    region = PostRegion(
        source=str(doc["source"]), posted_at=datetime.fromisoformat(doc["posted_at"]),
        region=str(doc["region"]), amount=_dec(doc.get("amount")),
        currency=str(doc.get("currency") or "USD"), amount_in_region=bool(doc.get("amount_in_region")),
        printed_code=doc.get("printed_code"), caption_code=doc.get("caption_code"),
        language=doc.get("language"), variant=doc.get("variant"), condition=doc.get("condition"),
        product_id=int(doc["product_id"]) if doc.get("product_id") is not None else None,
        grade_company=doc.get("grade_company"), grade=doc.get("grade"),
        is_multi=bool(doc.get("is_multi")), said_sold=bool(doc.get("said_sold")),
        source_url=str(doc.get("source_url") or ""), synthetic=bool(doc.get("synthetic")),
    )
    raw = doc.get("book")
    book = None
    if isinstance(raw, dict):
        book = Book(exit_low=_dec(raw.get("exit_low")), sales=int(raw.get("sales") or 0),
                    market=_dec(raw.get("market")), prior_low=_dec(raw.get("prior_low")),
                    high_day=_dec(raw.get("high_day")), currency=str(raw.get("currency") or "USD"))
    return region, book


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cardman.post")
    ap.add_argument("rows", help="JSONL. One photo region per line. A number is a claim, not a sale.")
    ap.add_argument("--store", help="write the claims; they stay out of the sales book")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    bad = 0
    observations = []
    for n, line in enumerate(Path(args.rows).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            region, book = region_from_json(json.loads(line))
            result = read_region(region, book)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            bad += 1
            print(f"BAD  line {n}  {type(exc).__name__}: {exc}")
            continue
        print(f"{(result.verdict or result.status):12} {result.card_key or '-'}  {result.why}")
        obs = to_observation(region, result, capture_time=now)
        if obs is not None:
            observations.append(obs)
    if args.store and observations:
        from .store import Store
        st = Store(args.store)
        added = 0
        for obs in observations:
            if st.add_observation(obs, now=now):
                added += 1
            key = obs.printing_candidates[0] if obs.printing_candidates else ""
            pool = st.observations_for(key, as_of=now) if key else []
            held, _ = select_comps(pool, key, as_of=now, include_synthetic=True)
            if any(c.id == obs.id for c in held):
                print(f"refused to keep going: {obs.id} was selected as a sale")
                return 2
        print(f"stored {added} claim(s); none selected as a sale")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
