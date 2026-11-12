"""Sales seen through eBay's own API: the sold count on fixed-price listings CardMan already tracks.

eBay's Browse API reports estimatedSoldQuantity on a fixed-price listing. Checking the same listing again later, a higher
count with an unchanged price means that many units sold at that price in between. That is recorded, and nothing else:
* the first check only sets a baseline (earlier sales have no date or known price);
* a listing that also takes Best Offers may have sold below its price, so its sales are kept as an upper bound only;
* a listing that disappears or ends is not a sale;
* a price change between checks makes the price of those sales unknown, so they are not recorded.
Each unit is one record keyed by item id + cumulative count, so a replayed check never counts a sale twice.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .ebay_browse import EbayBrowse, NotConfigured, SourceUnavailable
from .ebay_privacy import strip_user_fields
from .models import EvidenceType, MarketObservation

ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id?legacy_item_id={id}"
DAILY_CAP = 4000          # eBay's default Browse allowance is 5,000 calls a day; leave room for searches
PER_PASS = 120
RECHECK_AFTER = timedelta(hours=6)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ebay_sold_watch (item_id TEXT PRIMARY KEY, listing_key TEXT, checked_at TEXT, sold_qty INTEGER,
  price TEXT, currency TEXT, best_offer INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS ebay_api_calls (day TEXT PRIMARY KEY, n INTEGER NOT NULL);
"""


@dataclass
class WatchReport:
    checked: int = 0
    exposing_sold_count: int = 0
    sales_recorded: int = 0
    upper_bounds_recorded: int = 0
    skipped_price_changed: int = 0
    gone: int = 0
    errors: int = 0
    calls_today: int = 0


def _snapshot(item: dict) -> tuple[int | None, Decimal | None, str | None, bool, str]:
    ea = item.get("estimatedAvailabilities") or []
    sold = next((a.get("estimatedSoldQuantity") for a in ea if a.get("estimatedSoldQuantity") is not None), None)
    status = next((a.get("estimatedAvailabilityStatus") for a in ea if a.get("estimatedAvailabilityStatus")), "") or ""
    price = item.get("price") or {}
    p = Decimal(str(price["value"])) if price.get("value") is not None else None
    return (int(sold) if sold is not None else None), p, price.get("currency"), "BEST_OFFER" in (item.get("buyingOptions") or []), status


def check(engine, now: datetime, *, browse: EbayBrowse | None = None, budget: int = PER_PASS) -> WatchReport:
    """Re-read up to `budget` tracked fixed-price eBay listings, oldest check first, and record new unit sales."""
    db = engine.db
    db.executescript(_SCHEMA)
    rep = WatchReport()
    day = now.date().isoformat()
    used = (db.execute("SELECT n FROM ebay_api_calls WHERE day=?", (day,)).fetchone() or (0,))[0]
    budget = max(0, min(budget, DAILY_CAP - used))
    if not budget:
        rep.calls_today = used
        return rep
    try:
        browse = browse or EbayBrowse(engine.raw, min_interval_s=0.3)
        browse.token()
    except NotConfigured:
        return rep
    since = (now - timedelta(days=14)).isoformat()
    due = (now - RECHECK_AFTER).isoformat()
    # Rechecks first, fastest sellers first: only a second look at a listing can show a sale. New listings get the rest.
    rows = db.execute(
        "SELECT w.listing_key, w.item_id FROM ebay_sold_watch w JOIN listing l ON l.key=w.listing_key "
        "WHERE w.status<>'GONE' AND w.sold_qty IS NOT NULL AND w.checked_at<=? AND l.ended_at IS NULL AND l.last_seen>=? "
        "ORDER BY w.sold_qty DESC, w.checked_at LIMIT ?", (due, since, (budget * 2) // 3)).fetchall()
    rows += db.execute(
        "SELECT l.key, l.listing_id FROM listing l LEFT JOIN ebay_sold_watch w ON w.item_id=l.listing_id "
        "WHERE l.source LIKE 'ebay_browse%' AND l.kind='ask' AND l.ended_at IS NULL AND l.last_seen>=? AND w.item_id IS NULL "
        "ORDER BY l.first_seen DESC LIMIT ?", (since, budget - len(rows))).fetchall()
    for key, item_id in rows:
        req = urllib.request.Request(ITEM_URL.format(id=urllib.parse.quote(item_id)),
                                     headers={"Authorization": f"Bearer {browse.token()}", "X-EBAY-C-MARKETPLACE-ID": browse.marketplace})
        used += 1
        rep.checked += 1
        try:
            raw = browse._request(req)
        except SourceUnavailable as x:
            if "HTTP 404" in str(x):          # ended or removed: not a sale, just stop checking it
                with db:
                    db.execute("INSERT INTO ebay_sold_watch (item_id, listing_key, checked_at, status) VALUES (?,?,?, 'GONE') "
                               "ON CONFLICT(item_id) DO UPDATE SET status='GONE', checked_at=excluded.checked_at", (item_id, key, now.isoformat()))
                rep.gone += 1
            else:
                rep.errors += 1
            continue
        item = json.loads(raw)
        ref = engine.raw.put("ebay_item", json.dumps(strip_user_fields(item), ensure_ascii=False).encode(),
                             {"item_id": item_id, "captured_at": now.isoformat()})
        if "FIXED_PRICE" not in (item.get("buyingOptions") or []):
            continue
        sold, price, cur, best_offer, status = _snapshot(item)
        if sold is None:
            continue
        rep.exposing_sold_count += 1
        prev = db.execute("SELECT sold_qty, price, currency FROM ebay_sold_watch WHERE item_id=?", (item_id,)).fetchone()
        if prev and prev[0] is not None and sold > prev[0]:
            if prev[1] is None or price is None or Decimal(prev[1]) != price or prev[2] != cur:
                rep.skipped_price_changed += 1
            else:
                n = record_units(engine, key, item_id, prev[0], sold, price, cur, best_offer, now, ref, item)
                if best_offer:
                    rep.upper_bounds_recorded += n
                else:
                    rep.sales_recorded += n
        with db:
            db.execute("INSERT INTO ebay_sold_watch VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
                       "listing_key=excluded.listing_key, checked_at=excluded.checked_at, sold_qty=excluded.sold_qty, "
                       "price=excluded.price, currency=excluded.currency, best_offer=excluded.best_offer, status=excluded.status",
                       (item_id, key, now.isoformat(), sold, str(price) if price is not None else None, cur, int(best_offer), status or "LIVE"))
    with db:
        db.execute("INSERT INTO ebay_api_calls VALUES (?,?) ON CONFLICT(day) DO UPDATE SET n=excluded.n", (day, used))
    rep.calls_today = used
    return rep


def record_units(engine, key: str, item_id: str, before: int, after: int, price: Decimal, cur: str, best_offer: bool,
                 now: datetime, ref: str, item: dict) -> int:
    """One write-once record per unit sold between the last check and this one, priced at the listing's price."""
    from .intel import analyze
    title = item.get("title") or ""
    verified = engine.reviews(key)
    verified.pop("authenticity_ok", None)
    cands = tuple(p.id for p in analyze(title, engine.catalog, verified=verified).identity.candidates[:200])
    kind = EvidenceType.SELLER_CLAIM if best_offer else EvidenceType.MARKETPLACE_MARKED_SOLD
    note = ("sold between checks; listing also takes Best Offers, so the real price may be lower (upper bound only)" if best_offer
            else "sold between checks at the unchanged listing price, from eBay's sold count")
    added = 0
    for unit in range(before + 1, after + 1):
        o = MarketObservation(
            id=f"ebay_qty:{item_id}:{unit}", evidence_type=kind, source="ebay_browse:sold_count", original_amount=price, currency=cur,
            event_time=now, capture_time=now, source_url=item.get("itemWebUrl") or f"https://www.ebay.com/itm/{item_id}",
            printing_candidates=cands, listing_id=item_id, source_event_id=f"{item_id}:{unit}", raw_ref=ref,
            upstream_lineage=(f"ebay:{item_id}:{unit}",), status="VALID", status_reason=note)
        if engine.store.add_observation(o, now=now):
            added += 1
            with engine.db:
                engine.db.execute("CREATE TABLE IF NOT EXISTS import_row (batch TEXT NOT NULL, file TEXT, site TEXT, key TEXT NOT NULL, "
                                  "obs_id TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (batch, obs_id))")
                engine.db.execute("INSERT OR IGNORE INTO import_row VALUES (?,?,?,?,?,?)",
                                  (f"ebay-sold-{now.date().isoformat()}", "eBay sold counts", "ebay_api", key, o.id, now.isoformat()))
    return added
