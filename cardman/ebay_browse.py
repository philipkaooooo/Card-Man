"""eBay Browse API adapter: live listings only (CLAUDE.md section 2).

What this source can and cannot say:
* Browse returns ACTIVE listings. A fixed price is ACTIVE_ASKING; an auction with bids is
  OBSERVED_BID (the bid may never be paid). Neither is ever a sold comparable.
* A listing that accepts Best Offer has a list price that is not the accepted amount. Accepted
  offers live in eBay Product Research (seller account UI), which has no developer endpoint:
  import those through the manual CSV path with evidence_type ACCEPTED_OFFER.

Needs app keys (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET) for the OAuth client-credentials grant.
Status 2026-11-04: written from eBay's public API docs; no keys are set and api.ebay.com is
blocked by the environment's network policy, so no live request has been made yet.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from .evidence import RawEvidenceStore
from .identity import Catalog, ListingSignals, resolve
from .models import EvidenceType, IdentityStatus, MarketObservation

ADAPTER_VERSION = "ebay-browse-0.1.0"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class NotConfigured(RuntimeError):
    pass


class SourceUnavailable(RuntimeError):
    """Raised instead of returning an empty result, so an outage never looks like 'no supply'."""


@dataclass
class SearchResult:
    observations: list[MarketObservation]
    raw_refs: list[str]
    total: int | None
    pages: int
    titles: dict[str, str] = field(default_factory=dict)   # observation id -> seller's title


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def map_item(item: dict, *, captured_at: datetime, raw_ref: str, catalog: Catalog | None,
             marketplace: str, synthetic: bool = False) -> MarketObservation:
    """One itemSummary -> one immutable observation. Seller text is never treated as verified."""
    options = set(item.get("buyingOptions") or [])
    bid = item.get("currentBidPrice")
    bids = int(item.get("bidCount") or 0)
    if "AUCTION" in options and bid and bids > 0:
        et, price = EvidenceType.OBSERVED_BID, bid
    else:
        et, price = EvidenceType.ACTIVE_ASKING, item.get("price") or bid
    if not price or price.get("value") is None:
        raise ValueError(f"item {item.get('itemId')}: no price in payload")
    ship = None
    for opt in item.get("shippingOptions") or []:
        cost = opt.get("shippingCost")
        if cost and cost.get("currency") == price.get("currency"):
            ship = Decimal(str(cost["value"]))
            break
    notes = []
    if "BEST_OFFER" in options:
        notes.append("accepts Best Offer: list price is not the accepted amount")
    if ship is None and item.get("shippingOptions"):
        notes.append("shipping quoted in another currency or not quoted; left unset")
    title = item.get("title") or ""
    cands: tuple[str, ...] = ()
    if catalog is not None:
        # Title text is seller-entered: it can narrow candidates, never resolve them.
        res = resolve(ListingSignals(title=title, seller_set_code=title), catalog)
        if res.status in (IdentityStatus.CANDIDATES, IdentityStatus.RESOLVED):
            cands = tuple(p.id for p in res.candidates)
    created = _ts(item.get("itemCreationDate")) or captured_at
    return MarketObservation(
        id=f"ebay:{marketplace}:{item.get('itemId')}:{captured_at.isoformat()}",
        evidence_type=et, source=f"ebay_browse:{marketplace}",
        original_amount=Decimal(str(price["value"])), currency=price["currency"],
        event_time=captured_at,  # an asking price is a fact at observation time
        capture_time=captured_at, source_url=item.get("itemWebUrl") or "",
        printing_candidates=cands, listing_id=item.get("legacyItemId") or item.get("itemId"),
        # Never the readable username: a keyed hash that still groups one seller's listings (see ebay_privacy).
        seller_ref=seller_token((item.get("seller") or {}).get("username")), shipping_amount=ship,
        observed_condition=item.get("condition"), raw_ref=raw_ref, synthetic_fixture=synthetic,
        status="VALID", status_reason="; ".join(notes + [f"listed {created.isoformat()}",
                                                          f"bids={bids}" if "AUCTION" in options else ""]).strip("; "),
    )


from .ebay_privacy import seller_token, strip_user_fields  # noqa: E402


class EbayBrowse:
    def __init__(self, store: RawEvidenceStore, *, client_id: str | None = None, client_secret: str | None = None,
                 marketplace: str = "EBAY_US", min_interval_s: float = 1.0, max_retries: int = 4,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        self.client_id = client_id or os.environ.get("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET")
        self.store, self.marketplace = store, marketplace
        self.min_interval_s, self.max_retries = min_interval_s, max_retries
        self._open, self._sleep = opener, sleep
        self._token: tuple[str, float] | None = None
        self._last = 0.0

    def _request(self, req: urllib.request.Request) -> bytes:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                with self._open(req, timeout=30) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise SourceUnavailable(f"eBay HTTP {e.code}") from e
            except (urllib.error.URLError, OSError) as e:
                if attempt < self.max_retries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise SourceUnavailable(f"eBay unreachable: {e}") from e
        raise SourceUnavailable("retries exhausted")

    def token(self) -> str:
        if not (self.client_id and self.client_secret):
            raise NotConfigured("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(TOKEN_URL, method="POST",
                                     data=urllib.parse.urlencode({"grant_type": "client_credentials",
                                                                  "scope": SCOPE}).encode(),
                                     headers={"Authorization": f"Basic {basic}",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        body = json.loads(self._request(req))
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 7200)))
        return self._token[0]

    def search(self, q: str, *, catalog: Catalog | None = None, category_ids: str | None = None,
               limit: int = 50, max_pages: int = 3, now: datetime | None = None,
               synthetic: bool = False) -> SearchResult:
        """Paginated search. Raw pages are stored before parsing; an error raises, never returns []."""
        tok = self.token()
        now = now or datetime.now(timezone.utc)
        params = {"q": q, "limit": str(limit), "offset": "0"}
        if category_ids:
            params["category_ids"] = category_ids
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        obs, refs, total, pages = [], [], None, 0
        seen: set[str] = set()
        titles: dict[str, str] = {}
        while url and pages < max_pages:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                                       "X-EBAY-C-MARKETPLACE-ID": self.marketplace})
            raw = self._request(req)
            page = json.loads(raw)
            # Keep the raw page as evidence, minus seller identity; mapping below reads the full page in memory.
            ref = self.store.put("ebay_browse", json.dumps(strip_user_fields(page), ensure_ascii=False).encode(), {"url": url, "captured_at": now.isoformat(),
                                                     "adapter": ADAPTER_VERSION, "marketplace": self.marketplace})
            refs.append(ref)
            total = page.get("total", total)
            for item in page.get("itemSummaries") or []:
                if item.get("itemId") in seen:   # offsets can shift while paging
                    continue
                seen.add(item.get("itemId"))
                obs.append(map_item(item, captured_at=now, raw_ref=ref, catalog=catalog,
                                    marketplace=self.marketplace, synthetic=synthetic))
                titles[obs[-1].id] = item.get("title") or ""
            url = page.get("next")
            pages += 1
        return SearchResult(obs, refs, total, pages, titles)
