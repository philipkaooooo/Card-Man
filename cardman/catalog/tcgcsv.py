"""TCGplayer's catalog and prices through TCGCSV (https://tcgcsv.com): free, updated once a day around 20:00 UTC,
"permission is implicit" (FAQ). Rules from its docs: a custom User-Agent, ~100 ms between requests, well under
10,000 requests a day. Everything is cached under data/cache/tcgcsv/<day>/ and fetched at most once a day.

Category ids: 2 Yu-Gi-Oh!, 3 Pokémon (English), 85 Pokémon Japan.
Prices are TCGplayer's listing prices (low / mid / high) and its "market price"; one row per product and finish
(subTypeName: Normal, Holofoil, Reverse Holofoil, 1st Edition, Unlimited, ...). They are a price guide, not sales.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache" / "tcgcsv"
BASE = "https://tcgcsv.com"
UA = "CardMan/0.3 (private single-user catalog; contact via GitHub)"
CATEGORIES = {"ygo": 2, "pkm": 3}


def _get(url: str, tries: int = 4) -> dict | str:
    delay = 2.0
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                body = r.read()
            return body.decode() if url.endswith(".txt") else json.loads(body)
        except (urllib.error.URLError, TimeoutError, ValueError):
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def updated(get=_get) -> str:
    return str(get(f"{BASE}/last-updated.txt")).strip()


def sync(game: str, *, root: Path = CACHE, get=_get, pace: float = 0.12, log=print, only_groups: set[int] | None = None) -> Path:
    """Fetch groups, then products and prices of every group, for TCGCSV's current day. Returns the day folder."""
    cat = CATEGORIES[game]
    day = updated(get)[:10] or datetime.now(timezone.utc).date().isoformat()
    folder = root / day / str(cat)
    folder.mkdir(parents=True, exist_ok=True)
    gpath = folder / "groups.json"
    if not gpath.exists():
        gpath.write_text(json.dumps(get(f"{BASE}/tcgplayer/{cat}/groups")))
        time.sleep(pace)
    groups = json.loads(gpath.read_text())["results"]
    # A set's product list is reused from an earlier day when TCGplayer has not changed the set since (modifiedOn);
    # prices are fetched every day.
    prev = sorted(p for p in root.glob(f"*/{cat}") if p != folder and (p / "groups.json").exists())
    if prev:
        old = {g["groupId"]: g.get("modifiedOn") for g in json.loads((prev[-1] / "groups.json").read_text())["results"]}
        for g in groups:
            src = prev[-1] / f"{g['groupId']}.products.json"
            dst = folder / f"{g['groupId']}.products.json"
            if not dst.exists() and src.exists() and old.get(g["groupId"]) == g.get("modifiedOn"):
                dst.write_bytes(src.read_bytes())
    todo = [(g["groupId"], kind) for g in groups if not only_groups or g["groupId"] in only_groups
            for kind in ("products", "prices") if not (folder / f"{g['groupId']}.{kind}.json").exists()]
    import threading
    from concurrent.futures import ThreadPoolExecutor
    gate, last = threading.Lock(), [0.0]

    def one(job):
        gid, kind = job
        with gate:                                 # request starts at least `pace` apart, whatever the parallelism
            wait = pace - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
        try:
            data = get(f"{BASE}/tcgplayer/{cat}/{gid}/{kind}")
        except Exception as x:
            log(f"{game} group {gid} {kind}: {type(x).__name__}")
            return 0
        tmp = folder / f"{gid}.{kind}.json.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(folder / f"{gid}.{kind}.json")
        return 1
    with ThreadPoolExecutor(4) as ex:
        n = sum(ex.map(one, todo))
    log(f"{game}: {len(groups)} sets, {n} new files for {day}")
    return folder


def latest(game: str, root: Path = CACHE) -> Path | None:
    cat = str(CATEGORIES[game])
    days = sorted(p for p in root.glob(f"*/{cat}") if (p / "groups.json").exists())
    return days[-1] if days else None


def load(folder: Path) -> tuple[list[dict], dict[int, list[dict]], dict[int, list[dict]]]:
    groups = json.loads((folder / "groups.json").read_text())["results"]
    products, prices = {}, {}
    for g in groups:
        gid = g["groupId"]
        for kind, out in (("products", products), ("prices", prices)):
            p = folder / f"{gid}.{kind}.json"
            if p.exists():
                try:
                    out[gid] = json.loads(p.read_text()).get("results") or []
                except ValueError:
                    out[gid] = []
    return groups, products, prices
