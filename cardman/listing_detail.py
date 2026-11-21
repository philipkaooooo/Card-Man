"""A listing's own photos and the item details the seller filled in on eBay (card number, set, rarity, edition,
finish, language, grading), kept per eBay item id.

Where they come from, cheapest first:
  * the search results (main photo and thumbnails), already fetched every pass;
  * eBay's item lookup, already made for sold-count tracking (all photos and the item details);
  * the raw pages already on disk (backfill), so nothing is fetched twice.
Seller identity is stripped before anything is stored (ebay_privacy.strip_user_fields upstream).

Photos are served by CardMan itself from data/cache/listing_images (the page never loads anything from
another site), fetched on first view at a polite pace, capped in size, and removed 30 days after the listing ends.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "listing_images"
MAX_BYTES = 4 * 1024 * 1024
KEEP_AFTER_END = timedelta(days=30)
HOSTS = ("i.ebayimg.com",)
UA = "cardman/0.2 (private research tool)"
SIZES = {"s": 225, "m": 500, "l": 1600}

_SCHEMA = """CREATE TABLE IF NOT EXISTS listing_detail (item_id TEXT PRIMARY KEY, captured_at TEXT, images_json TEXT,
  aspects_json TEXT, epid TEXT, source TEXT);"""

# eBay's item-detail names -> our fields. Only what identifies the card or its condition.
KEEP = ("Game", "Card Name", "Set", "Card Number", "Rarity", "Features", "Edition", "Finish", "Language", "Graded",
        "Professional Grader", "Grade", "Card Condition", "Year Manufactured", "Speciality", "Autographed")


def ensure(db) -> None:
    db.executescript(_SCHEMA)


def _imgs(item: dict) -> list[str]:
    out = []
    for x in [item.get("image")] + list(item.get("additionalImages") or []) + list(item.get("thumbnailImages") or []):
        u = (x or {}).get("imageUrl")
        if u and _ok_url(u):
            base = _sized(u, "l")
            if base not in out:
                out.append(base)
    return out


def _ok_url(u: str) -> bool:
    m = re.match(r"https://([^/]+)/", u or "")
    return bool(m and m.group(1) in HOSTS)


def _sized(u: str, size: str) -> str:
    """eBay image URLs carry their size (…/s-l1600.jpg); ask for the one we need."""
    return re.sub(r"/s-l\d+\.(jpg|jpeg|png|webp)", f"/s-l{SIZES[size]}.\\1", u)


def aspects_of(item: dict) -> dict:
    asp = {"_buying": ",".join(item["buyingOptions"])} if item.get("buyingOptions") else {}
    for a in item.get("localizedAspects") or []:
        n, v = a.get("name"), a.get("value")
        if n in KEEP and v:
            asp[n] = str(v)[:200]
    return asp


def save(db, item_id: str, item: dict, now: datetime, source: str) -> None:
    """Merge what this payload adds: photos are unioned, item details replace older ones only if present."""
    ensure(db)
    imgs, asp = _imgs(item), aspects_of(item)
    row = db.execute("SELECT images_json, aspects_json, epid FROM listing_detail WHERE item_id=?", (item_id,)).fetchone()
    old_imgs = json.loads(row[0]) if row and row[0] else []
    merged = old_imgs + [u for u in imgs if u not in old_imgs] if len(imgs) <= len(old_imgs) else imgs + [u for u in old_imgs if u not in imgs]
    old_asp = json.loads(row[1]) if row and row[1] else {}
    with db:
        db.execute("INSERT OR REPLACE INTO listing_detail VALUES (?,?,?,?,?,?)",
                   (item_id, now.isoformat(), json.dumps(merged), json.dumps(asp or old_asp, ensure_ascii=False),
                    item.get("epid") or (row[2] if row else None), source if asp or not row else "merged"))


def get(db, item_id: str | None) -> dict:
    if not item_id:
        return {}
    ensure(db)
    row = db.execute("SELECT images_json, aspects_json, epid FROM listing_detail WHERE item_id=?", (item_id,)).fetchone()
    if not row:
        return {}
    return {"images": json.loads(row[0] or "[]"), "aspects": json.loads(row[1] or "{}"), "epid": row[2]}


def backfill(db, raw_dir: Path | str, now: datetime, *, log=print) -> tuple[int, int]:
    """Fill photos and item details from raw pages already on disk: item lookups, then search pages."""
    ensure(db)
    n_item = n_search = 0
    for f in sorted(glob.glob(str(Path(raw_dir) / "ebay_item" / "*.bin"))):
        try:
            item = json.loads(open(f, "rb").read())
        except Exception:
            continue
        iid = item.get("legacyItemId") or ((item.get("itemId") or "").split("|") + ["", ""])[1]
        if iid:
            save(db, str(iid), item, now, "item")
            n_item += 1
    have = {r[0] for r in db.execute("SELECT item_id FROM listing_detail")}
    for f in sorted(glob.glob(str(Path(raw_dir) / "ebay_browse" / "*.bin"))):
        try:
            page = json.loads(open(f, "rb").read())
        except Exception:
            continue
        for it in page.get("itemSummaries") or []:
            iid = it.get("legacyItemId")
            if iid and iid not in have:
                save(db, str(iid), it, now, "search")
                have.add(iid)
                n_search += 1
    log(f"details from {n_item} item lookups, photos from {n_search} search results")
    return n_item, n_search


# -- photos served by CardMan --------------------------------------------------------------------------------------------

_gate = threading.BoundedSemaphore(3)          # at most three photo downloads at once
_pace = threading.Lock()
_last = [0.0]


def _fetch(url: str) -> tuple[bytes, str]:
    with _gate:
        with _pace:                               # and no more than ~10 starts a second
            wait = 0.1 - (time.monotonic() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(MAX_BYTES + 1)
            return body, r.headers.get("Content-Type") or ""


def warm(db, item_ids, *, sizes=("m", "s"), limit: int = 150, root: Path | None = None, fetch=None) -> int:
    """Fetch the first photo of listings people will look at, before they look. Already cached ones cost nothing."""
    root = Path(root) if root is not None else CACHE
    done = 0
    for iid in item_ids:
        if done >= limit:
            break
        for sz in sizes:
            k = cache_key(iid, 0, sz)
            if not (root / k[:2] / f"{k}.img").exists() and photo(db, iid, 0, sz, root=root, fetch=fetch):
                done += 1
    return done


def cache_key(item_id: str, n: int, size: str) -> str:
    return hashlib.sha256(f"{item_id}:{n}:{size}".encode()).hexdigest()[:32]


def photo(db, item_id: str, n: int, size: str = "m", *, root: Path | None = None, fetch=None) -> tuple[bytes, str] | None:
    """The n-th photo of a listing at a size ('s' 225 px, 'm' 500 px, 'l' 1600 px), from cache or fetched once."""
    root = Path(root) if root is not None else CACHE
    fetch = fetch or _fetch
    if size not in SIZES or not re.fullmatch(r"\d{6,20}", item_id or "") or not 0 <= n < 24:
        return None
    imgs = get(db, item_id).get("images") or []
    if n >= len(imgs):
        return None
    url = _sized(imgs[n], size)
    if not _ok_url(url):
        return None
    k = cache_key(item_id, n, size)
    path = root / k[:2] / f"{k}.img"
    meta = path.with_suffix(".type")
    if path.exists():
        return path.read_bytes(), (meta.read_text() if meta.exists() else "image/jpeg")
    miss = path.with_suffix(".miss")
    if miss.exists() and time.time() - miss.stat().st_mtime < 6 * 3600:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        body, ctype = fetch(url)
    except Exception:
        miss.touch()
        return None
    ctype = ctype.split(";")[0].strip()
    if len(body) > MAX_BYTES or ctype not in ("image/jpeg", "image/png", "image/webp") or not body:
        miss.touch()
        return None
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
    meta.write_text(ctype)
    with open(root / "index.tsv", "a") as fh:          # which cache files belong to which listing, for eviction
        fh.write(f"{item_id}\t{k}\n")
    return body, ctype


def evict(db, now: datetime, *, root: Path | None = None) -> int:
    """Delete cached photos of listings that ended more than 30 days ago."""
    root = Path(root) if root is not None else CACHE
    idx = root / "index.tsv"
    if not idx.exists():
        return 0
    cutoff = (now - KEEP_AFTER_END).isoformat()
    ended = {r[0] for r in db.execute("SELECT listing_id FROM listing WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,))}
    keep, gone = [], 0
    for line in idx.read_text().splitlines():
        iid, _, k = line.partition("\t")
        if iid in ended:
            for ext in (".img", ".type", ".miss"):
                p = root / k[:2] / f"{k}{ext}"
                if p.exists():
                    p.unlink()
            gone += 1
        else:
            keep.append(line)
    idx.write_text("\n".join(keep) + ("\n" if keep else ""))
    return gone


# -- what the item details say about identity -----------------------------------------------------------------------------

_GRADERS = {"professional sports authenticator": "PSA", "psa": "PSA", "beckett": "BGS", "bgs": "BGS", "cgc": "CGC",
            "certified guaranty": "CGC", "sgc": "SGC", "ars": "ARS", "tag": "TAG", "ace": "ACE"}


def signals(aspects: dict) -> dict:
    """Plain identity facts from eBay's item details. Empty values are left out."""
    a = {k.lower(): v for k, v in (aspects or {}).items()}
    feats = " ".join(a.get(k, "") for k in ("features", "edition", "finish", "speciality")).lower()
    grader = None
    g = (a.get("professional grader") or "").lower()
    for k, v in _GRADERS.items():
        if k in g:
            grader = v
            break
    graded = (a.get("graded") or "").lower() == "yes"
    edition = ("1st Edition" if re.search(r"1st|first edition", feats) else "Shadowless" if "shadowless" in feats
               else "Unlimited" if "unlimited" in feats else None)
    finish = ("Reverse Holo" if re.search(r"reverse", feats) else "Non-holo" if re.search(r"non[\s-]*holo", feats)
              else "Holo" if "holo" in feats or "foil" in feats else None)
    game = a.get("game", "").lower()
    extra = a.get("features", "")
    return {k: v for k, v in {
        "game": "pkm" if "pok" in game else "ygo" if "yu-gi" in game or "yugi" in game else None,
        "name": a.get("card name"), "set": a.get("set"), "number": (a.get("card number") or "").strip() or None,
        "rarity": a.get("rarity"), "edition": edition, "finish": finish, "language": a.get("language"),
        "_features": extra,
        "grader": grader if graded or grader else None, "grade": re.sub(r"[^\d.]", "", a.get("grade") or "") or None if graded else None,
    }.items() if v}
