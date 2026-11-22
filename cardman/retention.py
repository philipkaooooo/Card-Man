"""Housekeeping: drop asks nobody would act on, keep every plausible deal.

A listing row survives the sweep if it is young (feed grace window), or the engine still
rates it worth acting on (ESCALATED/OPEN), or the owner touched it (card_listing, pile,
sold watch). Everything else — old bad and unrated asks — goes: the rows, the cached
photos, and the raw API archive for those items. Sold evidence (observation, ask_daily,
ebay_sold_watch) is never touched.

Dry run by default:

    python -m cardman.retention --store data/store/evidence.sqlite
    python -m cardman.retention --store data/store/evidence.sqlite --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

KEEP_STATUS = ("ESCALATED", "OPEN")
PHOTO_SIZES = ("s", "m", "l")


def _chunks(seq, n=900):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def pick_dead(db: sqlite3.Connection, now: datetime, days: int, max_open: int):
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    touch = ("SELECT key FROM card_listing "
             "UNION SELECT listing_key FROM ebay_sold_watch WHERE listing_key IS NOT NULL "
             "UNION SELECT key FROM card_pile")
    q = (f"SELECT l.key, l.listing_id FROM listing l LEFT JOIN opportunity o ON o.key=l.key "
         f"WHERE COALESCE(l.first_seen,'') < ? AND COALESCE(o.status,'') NOT IN {KEEP_STATUS!r} "
         f"AND l.key NOT IN ({touch}) ORDER BY l.first_seen ASC")
    dead = db.execute(q, (cutoff,)).fetchall()
    left = db.execute("SELECT COUNT(*) FROM listing").fetchone()[0] - len(dead)
    if left > max_open:      # still over the line: oldest unrated first, deals and youth stay
        seen = {k for k, _ in dead}
        dead += [r for r in db.execute(
            f"SELECT l.key, l.listing_id FROM listing l LEFT JOIN opportunity o ON o.key=l.key "
            f"WHERE COALESCE(o.status,'') NOT IN {KEEP_STATUS!r} AND l.key NOT IN ({touch}) "
            f"ORDER BY l.first_seen ASC LIMIT 40000").fetchall()
            if r[0] not in seen][: left - max_open]
    return dead


def sweep(db: sqlite3.Connection, root: str, now: datetime, days: int, max_open: int,
          raw_age: int, apply: bool) -> None:
    dead = pick_dead(db, now, days, max_open)
    keys = [k for k, _ in dead]
    dead_items = {i for _k, i in dead if i}
    print(f"stale asks to drop: {len(keys)} of {db.execute('SELECT COUNT(*) FROM listing').fetchone()[0]} open rows")

    freed = 0
    if keys:
        dead_hashes = set()
        for iid in dead_items:
            for n in range(24):
                for size in PHOTO_SIZES:
                    dead_hashes.add(hashlib.sha256(f"{iid}:{n}:{size}".encode()).hexdigest()[:32])
        shard = os.path.join(root, "data/cache/listing_images")
        for pre in sorted(os.listdir(shard)) if os.path.isdir(shard) else []:
            d = os.path.join(shard, pre)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f[:32] in dead_hashes:
                    p = os.path.join(d, f)
                    freed += os.path.getsize(p)
                    if apply:
                        os.remove(p)

    survivors = {r[0] for r in db.execute("SELECT listing_id FROM listing WHERE listing_id IS NOT NULL")} - dead_items
    arch = os.path.join(root, "data/raw/ebay_item")
    browse = os.path.join(root, "data/raw/ebay_browse")
    cut = time.time() - raw_age * 86400
    if os.path.isdir(arch):
        for f in os.listdir(arch):
            if not f.endswith(".meta.json"):
                continue
            p = os.path.join(arch, f)
            try:
                keep = json.load(open(p)).get("item_id") in survivors
            except (OSError, ValueError):
                keep = True
            if not keep:
                for x in (p, p[: -len(".meta.json")] + ".bin"):
                    if os.path.exists(x):
                        freed += os.path.getsize(x)
                        if apply:
                            os.remove(x)
    if os.path.isdir(browse):
        for f in os.listdir(browse):
            p = os.path.join(browse, f)
            if os.path.getmtime(p) < cut:
                freed += os.path.getsize(p)
                if apply:
                    os.remove(p)
    print(f"raw + photo files reclaimed: {freed / 1e6:.0f} MB")

    if apply and keys:
        for chunk in _chunks(keys):
            qk = ",".join("?" * len(chunk))
            ids = [r[0] for r in db.execute(f"SELECT listing_id FROM listing WHERE key IN ({qk})", chunk) if r[0]]
            db.execute(f"DELETE FROM listing WHERE key IN ({qk})", chunk)
            db.execute(f"DELETE FROM listing_analysis WHERE key IN ({qk})", chunk)
            db.execute(f"DELETE FROM opportunity WHERE key IN ({qk})", chunk)
            db.execute(f"DELETE FROM card_listing WHERE key IN ({qk})", chunk)
            if ids:
                qi = ",".join("?" * len(ids))
                db.execute(f"DELETE FROM listing_detail WHERE item_id IN ({qi})", ids)
        db.commit()
        db.execute("VACUUM")
        db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True)
    ap.add_argument("--days", type=int, default=7, help="an ask this old and not worth acting on is dead")
    ap.add_argument("--max-open", type=int, default=18000, help="hard ceiling on open ask rows")
    ap.add_argument("--raw-age", type=int, default=5, help="days before browse archives age out")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(a.store))))
    db = sqlite3.connect(a.store, timeout=30)
    sweep(db, root, datetime.now(timezone.utc).astimezone(), a.days, a.max_open, a.raw_age, a.apply)
    print("applied" if a.apply else "dry run — nothing deleted; pass --apply")


if __name__ == "__main__":
    main()
