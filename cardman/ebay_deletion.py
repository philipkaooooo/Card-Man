"""Apply eBay Marketplace Account Deletion notifications to everything stored on this machine.

The webhook queues verified notifications online; this drains them. A notification is confirmed back only after every
store and raw file has been purged, so an interruption means it is simply processed again.

Deletion is meant to be irreversible: identifiers are overwritten with secure_delete on, the database is vacuumed so freed
pages are gone, and only the notification id and counts are kept. Copies outside these files (Time Machine and other
backups) are not reached by this.

    python -m cardman.ebay_deletion sync
    python -m cardman.ebay_deletion purge --username NAME [--user-id ID] [--eias TOKEN]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .ebay_privacy import seller_token

ROOT = Path(__file__).resolve().parent.parent
REDACTED = "[deleted-user]"
LOG_SCHEMA = """CREATE TABLE IF NOT EXISTS ebay_deletion_log (notification_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL,
                rows_changed INTEGER NOT NULL, files_changed INTEGER NOT NULL)"""


@dataclass
class PurgeResult:
    rows: int = 0
    files: int = 0
    snapshots: int = 0


def _pattern(identifiers: list[str]) -> re.Pattern | None:
    ids = sorted({i for i in identifiers if i}, key=len, reverse=True)
    if not ids:
        return None
    # Whole values only: deleting user "sam" must not touch "Samurai" or "sam-2".
    return re.compile(r"(?<![\w.\-])(?:" + "|".join(map(re.escape, ids)) + r")(?![\w.\-])")


def _canonical_hash(data: dict) -> str:
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def purge_database(path: str | Path, identifiers: list[str]) -> PurgeResult:
    res = PurgeResult()
    rx = _pattern(identifiers)
    path = Path(path)
    if rx is None or not path.exists():
        return res
    ids = set(identifiers)
    db = sqlite3.connect(str(path), timeout=30)
    db.execute("PRAGMA secure_delete=ON")
    db.create_function("cm_has", 1, lambda v: 1 if isinstance(v, str) and rx.search(v) else 0, deterministic=True)
    db.create_function("cm_scrub", 1, lambda v: rx.sub(REDACTED, v) if isinstance(v, str) else v, deterministic=True)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    with db:
        # Structured seller fields become empty, not a shared placeholder that would look like one busy seller.
        if "observation" in tables:
            for oid, data in db.execute("SELECT id, data_json FROM observation WHERE cm_has(data_json)").fetchall():
                d = json.loads(data)
                if d.get("seller_ref") in ids:
                    d["seller_ref"] = None
                text = rx.sub(REDACTED, json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
                d = json.loads(text)
                db.execute("UPDATE observation SET data_json=?, content_hash=? WHERE id=?",
                           (json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":")), _canonical_hash(d), oid))
                res.rows += 1
        if "listing" in tables:
            res.rows += db.execute("UPDATE listing SET seller=NULL WHERE cm_has(seller)").rowcount
        if "market_signal" in tables:
            res.rows += db.execute("DELETE FROM market_signal WHERE cm_has(id) OR cm_has(printing_id) OR cm_has(detail_json)").rowcount
        if "snapshot" in tables:
            found = [r[0] for r in db.execute("SELECT id FROM snapshot WHERE cm_has(payload_json)")]
            for sid in found:
                db.execute("INSERT OR IGNORE INTO snapshot_redaction VALUES (?,?)", (sid, datetime.now(timezone.utc).isoformat()))
            res.snapshots = len(found)
        for t in tables:
            cols = [c[1] for c in db.execute(f'PRAGMA table_info("{t}")') if c[2].upper() == "TEXT" and not c[5]]
            for c in cols:
                res.rows += db.execute(f'UPDATE "{t}" SET "{c}"=cm_scrub("{c}") WHERE cm_has("{c}")').rowcount
    if res.rows:
        db.execute("VACUUM")
    db.close()
    return res


def purge_files(raw_root: str | Path, identifiers: list[str]) -> int:
    rx = _pattern(identifiers)
    root = Path(raw_root)
    if rx is None or not root.exists():
        return 0
    changed = 0
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_bytes().decode("utf-8", errors="surrogateescape")
        new = rx.sub(REDACTED, text)
        if new != text:
            with open(f, "r+b") as out:     # same blocks where the filesystem allows it
                out.write(new.encode("utf-8", errors="surrogateescape"))
                out.truncate()
                out.flush()
                os.fsync(out.fileno())
            changed += 1
    return changed


def default_paths() -> dict:
    return {"databases": [ROOT / "data" / "store" / "evidence.sqlite", ROOT / "data" / "cache" / "market.sqlite"], "raw": ROOT / "data" / "raw"}


def purge(records: list[dict], *, databases, raw, log_db, now: datetime | None = None) -> dict:
    """Purge every identifier in `records` in one pass; returns the ids safe to confirm back."""
    now = now or datetime.now(timezone.utc)
    identifiers = [v for r in records for v in (r.get("username"), r.get("userId"), r.get("eiasToken")) if v]
    # Sellers are stored as keyed hashes (ebay_privacy), so the hash must be erased as well as the plain value.
    identifiers += [seller_token(v) for v in list(identifiers)]
    rows = files = snapshots = 0
    for p in databases:
        r = purge_database(p, identifiers)
        rows, snapshots = rows + r.rows, snapshots + r.snapshots
    files = purge_files(raw, identifiers)
    Path(log_db).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(log_db), timeout=30)
    db.execute(LOG_SCHEMA)
    with db:
        for r in records:
            db.execute("INSERT OR REPLACE INTO ebay_deletion_log VALUES (?,?,?,?)", (r["notificationId"], now.isoformat(), rows, files))
    db.close()
    return {"processed": [r["notificationId"] for r in records], "rows": rows, "files": files, "snapshots": snapshots}


def _call(url: str, secret: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json", "User-Agent": "cardman/0.2"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def sync(*, url: str | None = None, secret: str | None = None, databases=None, raw=None, log_db=None, call=_call, max_batches: int = 20) -> dict:
    """Drain the online queue. Off (and silent) until EBAY_DELETIONS_URL and EBAY_DELETIONS_SECRET exist."""
    url = url or os.environ.get("EBAY_DELETIONS_URL")
    secret = secret or os.environ.get("EBAY_DELETIONS_SECRET")
    if not (url and secret):
        return {"configured": False, "processed": 0, "rows": 0, "files": 0}
    paths = default_paths()
    databases = databases if databases is not None else paths["databases"]
    raw = raw if raw is not None else paths["raw"]
    log_db = log_db or databases[0]
    total = {"configured": True, "processed": 0, "rows": 0, "files": 0}
    for _ in range(max_batches):
        records = call(url, secret)["deletions"]
        if not records:
            break
        out = purge(records, databases=databases, raw=raw, log_db=log_db)
        call(url, secret, {"processed": out["processed"]})     # only after every purge above succeeded
        total["processed"] += len(out["processed"])
        total["rows"] += out["rows"]
        total["files"] += out["files"]
    return total


def main(argv: list[str] | None = None) -> None:
    from .settings import load_env
    load_env()
    ap = argparse.ArgumentParser(prog="python -m cardman.ebay_deletion")
    ap.add_argument("cmd", choices=["sync", "purge"])
    ap.add_argument("--username")
    ap.add_argument("--user-id")
    ap.add_argument("--eias")
    a = ap.parse_args(argv)
    if a.cmd == "sync":
        print(json.dumps(sync()))
        return
    if not (a.username or a.user_id or a.eias):
        ap.error("purge needs --username, --user-id or --eias")
    p = default_paths()
    out = purge([{"notificationId": f"manual:{datetime.now(timezone.utc).isoformat()}", "username": a.username, "userId": a.user_id, "eiasToken": a.eias}],
                databases=p["databases"], raw=p["raw"], log_db=p["databases"][0])
    print(json.dumps({k: out[k] for k in ("rows", "files", "snapshots")}))


if __name__ == "__main__":
    main()
