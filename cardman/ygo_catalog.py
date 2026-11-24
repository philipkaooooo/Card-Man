"""The Yu-Gi-Oh! catalog and art, fetched once from YGOPRODeck and kept on this machine.

One call to cardinfo.php pulls every card with its passcode, sets, rarities and image URLs; the raw
response and a normalized SQLite index live under data/cache/ygoprodeck/. Full-size artwork is then
downloaded into data/cache/images/artwork/full/{passcode}.jpg - the exact path cardinfo.ImageCache
serves from - so the page never hotlinks and every card has a local picture. checkDBVer.php drives a
daily/weekly refresh that only fetches what changed. `sync` mirrors the catalog and art to a Cloud
Storage bucket so the set survives a wiped machine.

    python -m cardman.ygo_catalog bootstrap   # one full catalog fetch + index
    python -m cardman.ygo_catalog images      # download the full-size art (resumable)
    python -m cardman.ygo_catalog refresh     # checkDBVer, fetch + download only what is new
    python -m cardman.ygo_catalog sync        # mirror catalog + art to Cloud Storage
    python -m cardman.ygo_catalog status      # what is on disk
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
YGO_DIR = ROOT / "data" / "cache" / "ygoprodeck"
RAW = YGO_DIR / "cardinfo.json"
INDEX = YGO_DIR / "catalog.sqlite"
PROGRESS = YGO_DIR / "images_progress.json"
IMAGES_FULL = ROOT / "data" / "cache" / "images" / "artwork" / "full"

BASE = "https://db.ygoprodeck.com/api/v7"
CARDINFO = f"{BASE}/cardinfo.php"
DBVER = f"{BASE}/checkDBVer.php"
ART_URL = "https://images.ygoprodeck.com/images/cards/{id}.jpg"
UA = "cardman/0.2 (private research tool; caches images locally as YGOPRODeck asks)"
MISS_RETRY_S = 24 * 3600
DEFAULT_BUCKET = "gs://tb-web-assets/cardman/ygo"
DEFAULT_PROJECT = "tb-web-489108"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS card (
  id TEXT PRIMARY KEY, name TEXT, type TEXT, frame TEXT, race TEXT, attribute TEXT,
  archetype TEXT, atk INTEGER, def INTEGER, level INTEGER, data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS printing (
  id TEXT, set_code TEXT, set_name TEXT, rarity TEXT, rarity_code TEXT,
  PRIMARY KEY (id, set_code, rarity));
CREATE INDEX IF NOT EXISTS printing_code ON printing(set_code);
CREATE INDEX IF NOT EXISTS card_name ON card(name);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,image/jpeg"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def db_version() -> tuple[str, str]:
    """The current YGOPRODeck database version and its last-update stamp, from checkDBVer.php."""
    rows = json.loads(_get(DBVER, timeout=30))
    top = rows[0] if rows else {}
    return str(top.get("database_version") or ""), str(top.get("last_update") or "")


def fetch_catalog(save: bool = True) -> list[dict]:
    """One full cardinfo.php call: every card with passcode, sets, rarities, image URLs and prices."""
    cards = json.loads(_get(CARDINFO, timeout=180)).get("data") or []
    if save:
        YGO_DIR.mkdir(parents=True, exist_ok=True)
        tmp = RAW.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"data": cards}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(RAW)
    return cards


def normalize_card(c: dict) -> dict:
    imgs = c.get("card_images") or [{}]
    return {
        "id": str(c.get("id")), "name": c.get("name"), "type": c.get("type"),
        "frame": c.get("frameType"), "race": c.get("race"), "attribute": c.get("attribute"),
        "archetype": c.get("archetype"), "atk": c.get("atk"), "def": c.get("def"),
        "level": c.get("level") if c.get("level") is not None else c.get("linkval"),
        "desc": c.get("desc"), "archetype_of": c.get("archetype"),
        "sets": [{"set_code": (s.get("set_code") or "").upper(), "set_name": s.get("set_name"),
                  "rarity": s.get("set_rarity"), "rarity_code": s.get("set_rarity_code")}
                 for s in (c.get("card_sets") or [])],
        "image": imgs[0].get("image_url"), "image_small": imgs[0].get("image_url_small"),
        "image_cropped": imgs[0].get("image_url_cropped"),
        "prices": c.get("card_prices") or [{}],
    }


def build_index(cards: list[dict], version: str = "") -> int:
    """Write the normalized SQLite index from a card list. Returns the card count."""
    YGO_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(INDEX))
    db.executescript(_SCHEMA)
    rows, prints = [], []
    for c in cards:
        r = normalize_card(c)
        if not r["id"] or r["id"] == "None":
            continue
        rows.append((r["id"], r["name"], r["type"], r["frame"], r["race"], r["attribute"],
                     r["archetype"], r["atk"], r["def"], r["level"], json.dumps(r, ensure_ascii=False)))
        for s in r["sets"]:
            prints.append((r["id"], s["set_code"], s["set_name"], s["rarity"], s["rarity_code"]))
    with db:
        db.execute("DELETE FROM card")
        db.execute("DELETE FROM printing")
        db.executemany("INSERT OR REPLACE INTO card VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT OR REPLACE INTO printing VALUES (?,?,?,?,?)", prints)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('db_version', ?)", (version,))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('fetched_at', ?)", (_now(),))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('total', ?)", (str(len(rows)),))
    db.close()
    return len(rows)


def known_ids() -> set[str]:
    if not INDEX.exists():
        return set()
    db = sqlite3.connect(str(INDEX))
    ids = {r[0] for r in db.execute("SELECT id FROM card")}
    db.close()
    return ids


class _Rate:
    """A floor on the gap between requests so a bulk pull stays under YGOPRODeck's published limit."""

    def __init__(self, per_second: float):
        self.gap = 1.0 / per_second if per_second > 0 else 0.0
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        if not self.gap:
            return
        with self.lock:
            delta = time.monotonic() - self.last
            if delta < self.gap:
                time.sleep(self.gap - delta)
            self.last = time.monotonic()


def _one_image(passcode: str, rate: _Rate) -> str:
    """Download one full-size artwork into the ImageCache path. 'hit', 'skip', 'miss' or 'fail'."""
    if not passcode.isdigit():
        return "skip"
    path = IMAGES_FULL / f"{passcode}.jpg"
    if path.exists():
        return "hit"
    miss = path.with_suffix(".jpg.miss")
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_RETRY_S:
        return "skip"
    rate.wait()
    try:
        body = _get(ART_URL.format(id=passcode), timeout=30)
    except Exception:
        miss.touch()
        return "fail"
    if not body or len(body) < 64:
        miss.touch()
        return "miss"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".jpg.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
    return "hit"


def download_images(cards: list[dict] | None = None, workers: int = 8, rate: float = 12.0,
                    limit: int | None = None, log_every: int = 200) -> dict:
    """Fetch full-size art for every card, resumable: existing files are skipped, failures retry after a day."""
    if cards is None:
        cards = json.loads(RAW.read_text(encoding="utf-8")).get("data") or []
    ids = [str(c.get("id")) for c in cards if str(c.get("id") or "").isdigit()]
    if limit:
        ids = ids[:limit]
    IMAGES_FULL.mkdir(parents=True, exist_ok=True)
    rl = _Rate(rate)
    tally = {"hit": 0, "skip": 0, "miss": 0, "fail": 0}
    done = 0
    total = len(ids)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one_image, i, rl): i for i in ids}
        for fut in as_completed(futs):
            tally[fut.result()] += 1
            done += 1
            if done % log_every == 0 or done == total:
                PROGRESS.write_text(json.dumps({"at": _now(), "done": done, "total": total, **tally}))
                print(f"[images] {done}/{total}  new={tally['hit']} skip={tally['skip']} miss={tally['miss']} fail={tally['fail']}", flush=True)
    PROGRESS.write_text(json.dumps({"at": _now(), "done": done, "total": total, **tally}))
    return {"total": total, **tally}


def refresh(download: bool = True, workers: int = 8, rate: float = 12.0) -> dict:
    """Check the database version; if it moved, re-fetch the catalog and download only the new cards."""
    have = ""
    if INDEX.exists():
        db = sqlite3.connect(str(INDEX))
        row = db.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()
        db.close()
        have = row[0] if row else ""
    version, updated = db_version()
    if have and version == have:
        return {"changed": False, "db_version": version, "last_update": updated}
    before = known_ids()
    cards = fetch_catalog()
    build_index(cards, version)
    after = {str(c.get("id")) for c in cards if str(c.get("id") or "").isdigit()}
    new = sorted(after - before)
    out = {"changed": True, "db_version": version, "last_update": updated,
           "total": len(after), "new": len(new), "new_ids": new[:50]}
    if download and new:
        new_cards = [c for c in cards if str(c.get("id")) in set(new)]
        out["images"] = download_images(new_cards, workers=workers, rate=rate)
    return out


def cloud_sync(bucket: str = DEFAULT_BUCKET, project: str = DEFAULT_PROJECT,
               images: bool = True, dry_run: bool = False) -> dict:
    """Mirror the catalog and full-size art to Cloud Storage with gsutil -m rsync (additive, no deletes)."""
    bucket = bucket.rstrip("/")
    jobs = [(YGO_DIR, f"{bucket}/catalog")]
    if images:
        jobs.append((IMAGES_FULL, f"{bucket}/images/full"))
    results = []
    for src, dst in jobs:
        if not src.exists():
            results.append({"src": str(src), "skipped": "missing"})
            continue
        cmd = ["gsutil", "-m", "rsync", "-r"]
        if dry_run:
            cmd.append("-n")
        cmd += [str(src), dst]
        env = dict(os.environ, CLOUDSDK_CORE_PROJECT=project)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        results.append({"src": str(src), "dst": dst, "code": proc.returncode,
                        "tail": (proc.stdout or proc.stderr)[-400:]})
    return {"bucket": bucket, "project": project, "results": results}


def status() -> dict:
    out = {"catalog_dir": str(YGO_DIR), "raw_exists": RAW.exists(), "index_exists": INDEX.exists(),
           "images_full_dir": str(IMAGES_FULL)}
    if INDEX.exists():
        db = sqlite3.connect(str(INDEX))
        out["meta"] = dict(db.execute("SELECT key, value FROM meta"))
        out["cards"] = db.execute("SELECT COUNT(*) FROM card").fetchone()[0]
        out["printings"] = db.execute("SELECT COUNT(*) FROM printing").fetchone()[0]
        db.close()
    out["images_full"] = len(list(IMAGES_FULL.glob("*.jpg"))) if IMAGES_FULL.exists() else 0
    if PROGRESS.exists():
        out["progress"] = json.loads(PROGRESS.read_text())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cardman.ygo_catalog", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="one full catalog fetch + index")
    pi = sub.add_parser("images", help="download full-size art (resumable)")
    pi.add_argument("--workers", type=int, default=8)
    pi.add_argument("--rate", type=float, default=12.0, help="max requests per second")
    pi.add_argument("--limit", type=int, default=None)
    pr = sub.add_parser("refresh", help="checkDBVer, fetch + download only what is new")
    pr.add_argument("--no-images", action="store_true")
    pr.add_argument("--workers", type=int, default=8)
    pr.add_argument("--rate", type=float, default=12.0)
    ps = sub.add_parser("sync", help="mirror catalog + art to Cloud Storage")
    ps.add_argument("--bucket", default=DEFAULT_BUCKET)
    ps.add_argument("--project", default=DEFAULT_PROJECT)
    ps.add_argument("--no-images", action="store_true")
    ps.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="what is on disk")
    pd = sub.add_parser("daily", help="refresh then cloud sync - the scheduled job")
    pd.add_argument("--bucket", default=DEFAULT_BUCKET)
    pd.add_argument("--project", default=DEFAULT_PROJECT)
    pd.add_argument("--workers", type=int, default=8)
    pd.add_argument("--rate", type=float, default=12.0)
    a = ap.parse_args(argv)

    if a.cmd == "bootstrap":
        version, updated = db_version()
        cards = fetch_catalog()
        n = build_index(cards, version)
        print(json.dumps({"db_version": version, "last_update": updated, "cards": n, "index": str(INDEX)}))
    elif a.cmd == "images":
        print(json.dumps(download_images(workers=a.workers, rate=a.rate, limit=a.limit)))
    elif a.cmd == "refresh":
        print(json.dumps(refresh(download=not a.no_images, workers=a.workers, rate=a.rate)))
    elif a.cmd == "sync":
        print(json.dumps(cloud_sync(bucket=a.bucket, project=a.project, images=not a.no_images, dry_run=a.dry_run)))
    elif a.cmd == "status":
        print(json.dumps(status(), indent=2))
    elif a.cmd == "daily":
        r = refresh(download=True, workers=a.workers, rate=a.rate)
        s = cloud_sync(bucket=a.bucket, project=a.project)
        print(json.dumps({"refresh": r, "sync": s}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())