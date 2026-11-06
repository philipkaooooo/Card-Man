"""eBay user data in CardMan: keep as little as possible, and erase it on request.

eBay's Marketplace Account Deletion rule: when an eBay user closes their account, any personal data an app
stored about them must be deleted. CardMan only ever sees sellers (from public listings), and keeps:

* No seller username or user id in readable form. `seller_token()` replaces a username with a keyed hash
  (HMAC-SHA256 with a secret that never leaves this computer), so "same seller listed 12 items this week" still
  works, while the stored value can't be turned back into a name.
* Raw eBay pages are stored with the seller block removed (`strip_user_fields`).
* `erase_user()` removes whatever is left for one user (older records written before this module existed,
  listing rows, raw pages) and records that an erasure happened, without recording who.

Command line:  python -m cardman.ebay_privacy erase --username NAME [--user-id ID]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SALT_FILE = ROOT / "data" / "privacy_salt"
USER_KEYS = ("seller", "username", "userId", "feedbackScore", "feedbackPercentage", "sellerAccountType",
             "sellerLegalInfo", "contactInfo")


def _salt() -> bytes:
    env = os.environ.get("CARDMAN_PRIVACY_SALT")
    if env:
        return env.encode()
    if not SALT_FILE.exists():
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            SALT_FILE.write_text(secrets.token_hex(32))
        finally:
            os.umask(old)
    return SALT_FILE.read_text().strip().encode()


def seller_token(username: str | None) -> str | None:
    if not username:
        return None
    if username.startswith("seller:"):
        return username
    return "seller:" + hmac.new(_salt(), username.strip().lower().encode(), hashlib.sha256).hexdigest()[:20]


def strip_user_fields(obj):
    """A copy of an eBay API payload with seller identity removed (prices, titles and item ids stay)."""
    if isinstance(obj, dict):
        return {k: strip_user_fields(v) for k, v in obj.items() if k not in USER_KEYS}
    if isinstance(obj, list):
        return [strip_user_fields(v) for v in obj]
    return obj


def erase_user(store_path: str | Path, raw_dir: str | Path | None = None, *, username: str | None = None,
               user_id: str | None = None, now: datetime | None = None) -> dict:
    """Delete one eBay user's data from the store and raw pages. Returns counts only."""
    now = now or datetime.now(timezone.utc)
    names = {x for x in (username, user_id) if x}
    if not names:
        raise ValueError("need a username or user id")
    tokens = {seller_token(x) for x in names}
    matches = names | tokens | {n.lower() for n in names}
    db = sqlite3.connect(str(store_path))
    counts = {"observations": 0, "listings": 0, "raw_files": 0}
    from .store import content_hash
    with db:
        rows = db.execute("SELECT id, data_json FROM observation").fetchall()
        for oid, dj in rows:
            d = json.loads(dj)
            ref = d.get("seller_ref")
            if ref and (ref in matches or ref.lower() in matches):
                d["seller_ref"] = None
                # Erasure is the one sanctioned rewrite of a stored fact; the hash follows the new content.
                db.execute("UPDATE observation SET data_json=?, content_hash=? WHERE id=?",
                           (json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                            content_hash(d), oid))
                counts["observations"] += 1
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing'").fetchone():
            for col in ("seller",):
                cur = db.execute(f"UPDATE listing SET {col}=NULL WHERE {col} IN ({','.join('?' * len(matches))})",
                                 tuple(matches))
                counts["listings"] += cur.rowcount
        db.execute("CREATE TABLE IF NOT EXISTS privacy_erasure (at TEXT, source TEXT, counts_json TEXT)")
    if raw_dir and Path(raw_dir).exists():
        for f in Path(raw_dir).rglob("*"):
            if not f.is_file() or f.suffix not in (".bin", ".json"):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(n in text for n in names):
                try:
                    cleaned = json.dumps(strip_user_fields(json.loads(text)), ensure_ascii=False)
                except json.JSONDecodeError:
                    cleaned = text
                for n in names:
                    cleaned = cleaned.replace(n, "[erased]")
                f.write_text(cleaned, encoding="utf-8")
                counts["raw_files"] += 1
    with db:
        db.execute("INSERT INTO privacy_erasure VALUES (?,?,?)", (now.isoformat(), "ebay_account_deletion",
                                                                 json.dumps(counts)))
    db.close()
    return counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.ebay_privacy")
    ap.add_argument("cmd", choices=["erase"])
    ap.add_argument("--username")
    ap.add_argument("--user-id")
    ap.add_argument("--store", default=str(ROOT / "data" / "store" / "evidence.sqlite"))
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    a = ap.parse_args(argv)
    print(json.dumps(erase_user(a.store, a.raw, username=a.username, user_id=a.user_id)))


if __name__ == "__main__":
    main()
