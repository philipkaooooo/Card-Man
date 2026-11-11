"""Market data routes behind one interface. Each route says whether it is on and why; a route that is off stays off
quietly and never reads as "no supply".

Routes today:
* inbox: files you saved or exported into data/inbox (CSV, JSON, JSONL, saved HTML pages). Works for any marketplace,
  needs no permission from us, keeps the original file, and is how Yahoo! Auctions, Mercari, Ruten and shop data enter
  until an automated route has been cleared against that site's terms.
* ebay: Browse API, off until EBAY_CLIENT_ID / EBAY_CLIENT_SECRET exist. Nothing else depends on it.
* ygoprodeck: aggregate reference prices per set code. Never comparables.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

from .evidence import RawEvidenceStore
from .models import EvidenceType, MarketObservation

MARKET_OF_SOURCE = {"yahoo_auctions_jp": "JP", "yahoo_jp": "JP", "mercari_jp": "JP", "mercari": "JP", "rakuma": "JP", "yuyutei": "JP",
                    "cardrush": "JP", "snkrdunk": "JP", "magi": "JP", "ruten": "TW", "shopee_tw": "TW", "yahoo_tw": "TW", "fb_tw": "TW",
                    "ebay": "INTL", "ebay_browse": "INTL", "tcgplayer": "INTL", "cardmarket": "INTL", "pwcc": "INTL", "goldin": "INTL"}
KINDS = {"ask": EvidenceType.ACTIVE_ASKING, "bid": EvidenceType.OBSERVED_BID, "auction": EvidenceType.OBSERVED_BID,
         "sold": EvidenceType.MARKETPLACE_MARKED_SOLD, "accepted_offer": EvidenceType.ACCEPTED_OFFER,
         "transaction": EvidenceType.INDEPENDENT_TRANSACTION, "unsold": EvidenceType.AUCTION_ENDED_UNSOLD,
         # eBay "Best Offer accepted" with the price struck through: the real amount is hidden, the shown one is a ceiling
         "best_offer_hidden": EvidenceType.SELLER_CLAIM}
VERIFIED_FIELDS = ("printed_set_code", "verified_rarity", "verified_edition", "verified_product", "card_language")


def market_for(source: str, currency: str | None = None) -> str:
    base = source.split(":")[0].lower()
    if base in MARKET_OF_SOURCE:
        return MARKET_OF_SOURCE[base]
    return {"JPY": "JP", "TWD": "TW"}.get((currency or "").upper(), "INTL")


@dataclass
class RawListing:
    source: str
    listing_id: str
    title: str
    price: Decimal
    currency: str
    kind: str = "ask"
    url: str = ""
    description: str = ""
    shipping: Decimal | None = None
    seller: str | None = None
    event_time: datetime | None = None
    ends_at: datetime | None = None
    bids: int | None = None
    photos: tuple[str, ...] = ()
    market: str | None = None
    printing_id: str | None = None        # explicit identification by you, in an import
    grade_company: str | None = None
    grade: str | None = None
    condition: str | None = None
    verified: dict = field(default_factory=dict)
    raw_ref: str | None = None
    synthetic: bool = False
    lineage: str | None = None          # e.g. "ebay:123": copies of one sale from different sites count once
    is_lot: bool = False
    quantity: int = 1

    def __post_init__(self):
        self.currency = self.currency.upper()
        if self.kind not in KINDS:
            raise ValueError(f"unknown listing kind {self.kind!r}")
        self.market = self.market or market_for(self.source, self.currency)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"

    def fingerprint(self) -> str:
        d = {k: str(v) for k, v in asdict(self).items() if k not in ("raw_ref",)}
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    def to_observation(self, printing_candidates: tuple[str, ...], captured_at: datetime) -> MarketObservation:
        event = self.event_time or captured_at
        return MarketObservation(
            id=f"{self.key}:{self.kind}:{event.isoformat()}:{self.price}", evidence_type=KINDS[self.kind], source=self.source,
            original_amount=self.price, currency=self.currency, event_time=event, capture_time=captured_at, source_url=self.url,
            printing_candidates=printing_candidates, listing_id=self.listing_id, seller_ref=self.seller, shipping_amount=self.shipping,
            grade_company=self.grade_company, grade=self.grade, observed_condition=self.condition, raw_ref=self.raw_ref,
            synthetic_fixture=self.synthetic, upstream_lineage=(self.lineage,) if self.lineage else (), is_lot=self.is_lot,
            quantity=self.quantity)


# -- parsing files ----------------------------------------------------------------------------------------------------

def _dec(v) -> Decimal | None:
    if v in (None, ""):
        return None
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    try:
        return Decimal(s) if s else None
    except InvalidOperation:
        return None


def _when(v) -> datetime | None:
    if not v:
        return None
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def from_record(r: dict, *, default_source: str = "inbox", raw_ref: str | None = None) -> RawListing:
    """One dict in the inbox format. Unknown keys are ignored; a row without title or price is refused by name."""
    title, price = r.get("title"), _dec(r.get("price") if r.get("price") not in (None, "") else r.get("amount"))
    if not title or price is None or not r.get("currency"):
        raise ValueError(f"record needs title, price and currency: {str(r)[:120]}")
    source = r.get("source") or default_source
    url = r.get("url") or r.get("source_url") or ""
    lid = str(r.get("listing_id") or r.get("id") or hashlib.sha256(f"{source}{url}{title}{price}".encode()).hexdigest()[:16])
    photos = r.get("photos") or ()
    if isinstance(photos, str):
        photos = tuple(x for x in photos.split("|") if x)
    return RawListing(source=source, listing_id=lid, title=title, price=price, currency=r["currency"], kind=(r.get("kind") or "ask").lower(),
                      url=url, description=r.get("description") or "", shipping=_dec(r.get("shipping")), seller=r.get("seller") or None,
                      event_time=_when(r.get("event_time")), ends_at=_when(r.get("ends_at")),
                      bids=int(r["bids"]) if str(r.get("bids") or "").isdigit() else None, photos=tuple(photos), market=r.get("market") or None,
                      printing_id=r.get("printing_id") or None, grade_company=r.get("grade_company") or None, grade=r.get("grade") or None,
                      condition=r.get("condition") or None, verified={k: r[k] for k in VERIFIED_FIELDS if r.get(k)}, raw_ref=raw_ref,
                      synthetic=str(r.get("synthetic", "")).lower() == "true")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks, self._in, self.title = [], False, ""
        self._title = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._in = True
            self.blocks.append("")
        elif tag == "title":
            self._title = True

    def handle_endtag(self, tag):
        self._in = False if tag == "script" else self._in
        self._title = False if tag == "title" else self._title

    def handle_data(self, data):
        if self._in:
            self.blocks[-1] += data
        elif self._title:
            self.title += data


def parse_saved_html(html: str, *, source: str, url: str = "", raw_ref: str | None = None) -> list[RawListing]:
    """schema.org Product/Offer data in a page you saved. Sites without it yield nothing, and the caller reports that."""
    p = _Scripts()
    p.feed(html)
    out = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if "@graph" in node:
                walk(node["@graph"])
            types = node.get("@type")
            if types == "Product" or (isinstance(types, list) and "Product" in types):
                offers = node.get("offers") or {}
                offers = offers[0] if isinstance(offers, list) and offers else offers
                price, cur = offers.get("price") or offers.get("lowPrice"), offers.get("priceCurrency")
                if price is not None and cur and node.get("name"):
                    avail = str(offers.get("availability") or "")
                    kind = "sold" if any(w in avail for w in ("SoldOut", "OutOfStock", "Discontinued")) else "ask"
                    out.append(from_record({"source": source, "title": node["name"], "price": price, "currency": cur, "kind": kind,
                                            "url": offers.get("url") or node.get("url") or url, "description": node.get("description") or "",
                                            "listing_id": node.get("sku") or node.get("productID") or None},
                                           default_source=source, raw_ref=raw_ref))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
    for b in p.blocks:
        try:
            walk(json.loads(b))
        except json.JSONDecodeError:
            continue
    return out


SOURCE_OF_SITE = {"ebay": "ebay", "130point": "130point", "yahoo_auctions_jp": "yahoo_auctions_jp", "mercari_jp": "mercari_jp"}


def sold_rows_to_listings(page, raw_ref: str | None) -> list[RawListing]:
    out = []
    for r in page.rows:
        f = r.flags()
        notes = "; ".join(r.notes)
        out.append(RawListing(source=SOURCE_OF_SITE.get(r.site, r.site), listing_id=r.item_id, title=r.title, price=r.price,
                              currency=r.currency, kind=r.kind, url=r.url, description=notes, shipping=r.shipping, seller=None,
                              event_time=r.sold_at, grade_company=f["grade_company"], grade=f["grade"], condition=f["condition"],
                              raw_ref=raw_ref, synthetic=page.synthetic, lineage=r.lineage, is_lot=f["is_lot"],
                              quantity=f["lot_size"] or 1 if f["is_lot"] else 1))
    return out


def parse_bytes(name: str, data: bytes, *, raw_ref: str | None = None) -> tuple[list[RawListing], list[str]]:
    """(listings, row errors). `source` defaults to the file's first dotted name part, e.g. mercari_jp.2026-11.csv."""
    src = Path(name).name.split(".")[0].lower() or "inbox"
    ext = Path(name).suffix.lower()
    text = data.decode("utf-8-sig", errors="replace")
    rows, errors, out = [], [], []
    if ext == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
    elif ext == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif ext == ".json":
        d = json.loads(text)
        rows = d if isinstance(d, list) else (d.get("listings") or [d])
    elif ext in (".html", ".htm", ".webarchive"):
        from .sold_pages import read_page
        page = read_page(name, data, datetime.now(timezone.utc))
        if page.rows:
            return sold_rows_to_listings(page, raw_ref), []
        if ext == ".webarchive":
            return [], [f"{name}: no sold results recognized in this page"]
        found = parse_saved_html(text, source=src, raw_ref=raw_ref)
        return found, ([] if found else [f"{name}: no schema.org Product data in this page; export a CSV instead"])
    else:
        return [], [f"{name}: unsupported file type {ext}"]
    for i, r in enumerate(rows, 1):
        try:
            out.append(from_record(r, default_source=src, raw_ref=raw_ref))
        except (ValueError, KeyError, TypeError) as x:
            errors.append(f"{name} row {i}: {x}")
    return out, errors


# -- adapters ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteStatus:
    name: str
    on: bool
    detail: str
    capability: str


class InboxAdapter:
    name = "inbox"
    capability = "any marketplace, via files you save"

    def __init__(self, folder: Path | str, raw: RawEvidenceStore, seen):
        self.folder, self.raw, self.seen = Path(folder), raw, seen

    def status(self) -> RouteStatus:
        n = len([f for f in self.folder.glob("*") if f.is_file()]) if self.folder.exists() else 0
        return RouteStatus(self.name, True, f"watching {self.folder} ({n} file(s))", self.capability)

    def poll(self, now: datetime) -> tuple[list[RawListing], list[str]]:
        self.folder.mkdir(parents=True, exist_ok=True)
        listings, errors = [], []
        for f in sorted(self.folder.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            data = f.read_bytes()
            h = hashlib.sha256(data).hexdigest()
            if self.seen(h):        # the same bytes are only ever read once
                continue
            stored = data
            if f.suffix.lower() in (".html", ".htm", ".webarchive"):
                # Saved web pages carry seller names and profiles: only the copy with people removed is kept.
                from .sold_pages import from_webarchive, parse_html, serialize, strip_people
                html_text = from_webarchive(data) if f.suffix.lower() == ".webarchive" else data.decode("utf-8", errors="replace")
                stored = serialize(strip_people(parse_html(html_text))).encode()
            ref = self.raw.put("inbox", stored, {"file": f.name, "captured_at": now.isoformat(), "sha256_original": h})
            got, errs = parse_bytes(f.name, data, raw_ref=ref)
            listings += got
            errors += errs
            self.seen(h, mark=f.name)
        return listings, errors


class EbayAdapter:
    """Off until keys exist. Checks the environment only when asked, so nothing fails at import or startup."""
    name = "ebay"
    capability = "live eBay asks and bids; never sold prices"

    def __init__(self, raw: RawEvidenceStore, queries: list[str], catalog=None):
        self.raw, self.queries, self.catalog = raw, queries, catalog

    def status(self) -> RouteStatus:
        if not (os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET")):
            return RouteStatus(self.name, False, "waiting for eBay developer approval; set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to turn on",
                               self.capability)
        if not self.queries:
            return RouteStatus(self.name, False, "keys present, no searches configured", self.capability)
        return RouteStatus(self.name, True, f"{len(self.queries)} search(es)", self.capability)

    def poll(self, now: datetime) -> tuple[list[RawListing], list[str]]:
        if not self.status().on:
            return [], []
        from .ebay_browse import EbayBrowse, SourceUnavailable
        client = EbayBrowse(self.raw)
        out, errors, seen = [], [], set()
        for q in self.queries:
            try:
                res = client.search(q, catalog=self.catalog, now=now)
            except SourceUnavailable as x:
                errors.append(f"ebay '{q}': {x}")
                continue
            for o in res.observations:
                if o.id in seen:      # the same item matched an earlier search this pass
                    continue
                seen.add(o.id)
                out.append(RawListing(source=o.source, listing_id=o.listing_id or o.id, title=res.titles.get(o.id) or q,
                                      price=o.original_amount, currency=o.currency,
                                      kind="bid" if o.evidence_type == EvidenceType.OBSERVED_BID else "ask", url=o.source_url,
                                      shipping=o.shipping_amount, seller=o.seller_ref, condition=o.observed_condition, raw_ref=o.raw_ref))
        return out, errors


class YgoprodeckReference:
    """Per-code reference price from YGOPRODeck. An aggregate estimate: a research hint, never a sale."""
    name = "ygoprodeck"
    capability = "reference price per English code; not sales"
    URL = "https://db.ygoprodeck.com/api/v7/cardsetsinfo.php?setcode={code}"

    def __init__(self, db, get=None):
        self.db = db
        self.get = get or self._http
        db.execute("CREATE TABLE IF NOT EXISTS reference_price (code TEXT, day TEXT, rarity TEXT, usd TEXT, name TEXT, PRIMARY KEY (code, day))")

    @staticmethod
    def _http(url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "cardman/0.2 (private research tool)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def status(self) -> RouteStatus:
        return RouteStatus(self.name, True, "on, prices reused for a week per code", self.capability)

    FRESH_DAYS = 7      # an aggregate guide price moves slowly; a lead does not need a daily refresh

    def price(self, code: str, now: datetime) -> tuple[Decimal, str] | None:
        day = now.date().isoformat()
        since = (now - timedelta(days=self.FRESH_DAYS)).date().isoformat()
        # A price is reused for a week; an empty answer (or a failed lookup) only for the day it was asked.
        row = self.db.execute("SELECT usd, rarity FROM reference_price WHERE code=? AND ((usd<>'' AND day>=?) OR day=?) "
                              "ORDER BY day DESC LIMIT 1", (code, since, day)).fetchone()
        if row is None:
            try:
                d = self.get(self.URL.format(code=urllib.parse.quote(code)))
            except Exception:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO reference_price VALUES (?,?,?,?,?)", (code, day, "", "", ""))
                return None
            usd = d.get("set_price") if isinstance(d, dict) else None
            row = (str(usd) if usd else "", d.get("set_rarity") or "" if isinstance(d, dict) else "")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO reference_price VALUES (?,?,?,?,?)", (code, day, row[1], row[0], (d or {}).get("name", "")))
        return (Decimal(row[0]), row[1]) if row[0] and Decimal(row[0]) > 0 else None
