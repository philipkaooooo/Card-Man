"""Pokémon TCG (English / international) catalog: every set and card from the Pokémon TCG API, split into the
versions a collector prices separately, kept locally so nothing is fetched twice.

Source: api.pokemontcg.io v2. No key needed (1,000 requests a day, 30 a minute); a full sync is about 80 requests,
paced to one every 2.2 s. The API is being retired (existing keys work to 1 March 2027, no new keys); the raw pages
are kept under data/cache/pkm so a later move to another source (TCGdex, which also has Japanese sets) can rebuild
from the same shape.

Identity: the card is set id + number (sv3pt5-199). A version is one way that card was printed:
  Normal, Holo, Reverse Holo, 1st Edition (Holo), Unlimited (Holo), Shadowless (Base Set only), Promo.
The versions come from the price fields the source lists for each card (they exist only for real versions), from
data/pkm_variant_rules.json for what the source does not list (Shadowless), and from the rarity when there are none.

Printing ids:  pk:EN:<card id>:<version slug>@pkm-<card id>     e.g.  pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199
The part after '@' is the card key, the Pokémon counterpart of a Yu-Gi-Oh! passcode. The two never overlap.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .models import CardFamily, Printing, Region

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "cache" / "pkm"
DB = ROOT / "data" / "cache" / "pkm.sqlite"
RULES = ROOT / "data" / "pkm_variant_rules.json"
API = "https://api.pokemontcg.io/v2"
UA = "CardMan/0.1 (private, single-user catalog sync)"
PACE_S = 2.2

# tcgplayer price keys -> version name, in the order a collector lists them
PRICE_KEYS = {"1stEditionHolofoil": "1st Edition Holo", "1stEditionNormal": "1st Edition", "1stEdition": "1st Edition",
              "unlimitedHolofoil": "Unlimited Holo", "unlimited": "Unlimited", "holofoil": "Holo", "normal": "Normal",
              "reverseHolofoil": "Reverse Holo"}
ORDER = ["1st Edition Holo", "1st Edition", "Shadowless", "Unlimited Holo", "Unlimited", "Holo", "Normal", "Reverse Holo", "Promo"]


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def card_key(card_id: str) -> str:
    return f"pkm-{card_id}"


def printing_id(card_id: str, version: str) -> str:
    return f"pk:EN:{card_id}:{slug(version)}@{card_key(card_id)}"


# -- fetching ---------------------------------------------------------------------------------------------------------

def _get(url: str, tries: int = 5) -> dict:
    delay = 5.0
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def sync(raw: Path = RAW, *, log=print, get=_get, pace: float = PACE_S, only: list[str] | None = None) -> int:
    """Download every set, then each set's cards (one request per set, resumable: sets already saved are skipped).
    The API's deep paging fails often; per-set requests are small and steady. Returns the number of cards saved."""
    raw.mkdir(parents=True, exist_ok=True)
    sets = get(f"{API}/sets?pageSize=250&orderBy=releaseDate")
    (raw / "sets.json.gz").write_bytes(gzip.compress(json.dumps(sets).encode()))
    log(f"{len(sets['data'])} sets")
    n, missing = 0, []
    for s in sets["data"]:
        sid = s["id"]
        if only and sid not in only:
            continue
        out = raw / f"set-{sid}.json.gz"
        if out.exists():
            n += len(json.loads(gzip.decompress(out.read_bytes()))["data"])
            continue
        cards, page = [], 1
        try:
            while True:
                time.sleep(pace)
                d = get(f"{API}/cards?q=set.id:{sid}&pageSize=250&page={page}")
                cards += d["data"]
                if len(cards) >= (d.get("totalCount") or 0) or not d["data"]:
                    break
                page += 1
        except Exception as x:
            missing.append(sid)
            log(f"{sid}: {type(x).__name__} {x} (will retry next run)")
            continue
        out.write_bytes(gzip.compress(json.dumps({"data": cards}).encode()))
        n += len(cards)
        log(f"{sid}: {len(cards)} cards (total {n})")
    if missing:
        log(f"not yet: {', '.join(missing)}")
    return n


# -- versions ---------------------------------------------------------------------------------------------------------

def _rules(path: Path = RULES) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def versions_of(card: dict, rules: dict | None = None) -> list[str]:
    rules = rules if rules is not None else _rules()
    keys = list(((card.get("tcgplayer") or {}).get("prices") or {}).keys())
    out = [PRICE_KEYS[k] for k in keys if k in PRICE_KEYS]
    sid = (card.get("set") or {}).get("id", "")
    for extra in (rules.get("extra_versions") or {}).get(sid, []):
        if extra.get("rarity_contains", "").lower() in (card.get("rarity") or "").lower():
            out.append(extra["version"])
    if (card.get("set") or {}).get("series") == "Promo" or "Promo" in (card.get("rarity") or ""):
        out = out or ["Promo"]
    if not out:
        rar = (card.get("rarity") or "").lower()
        out = ["Holo" if ("holo" in rar or "rare" in rar and "common" not in rar) else "Normal"]
    seen = []
    for v in out:
        if v not in seen:
            seen.append(v)
    return sorted(seen, key=lambda v: ORDER.index(v) if v in ORDER else 99)


# -- index ------------------------------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pkm_set (id TEXT PRIMARY KEY, name TEXT, series TEXT, printed_total INTEGER, total INTEGER,
  release TEXT, ptcgo TEXT, logo TEXT, symbol TEXT);
CREATE TABLE IF NOT EXISTS pkm_card (id TEXT PRIMARY KEY, name TEXT, norm TEXT, set_id TEXT, number TEXT, supertype TEXT,
  rarity TEXT, artist TEXT, regmark TEXT, release TEXT, json TEXT);
CREATE INDEX IF NOT EXISTS pkm_card_norm ON pkm_card(norm);
CREATE INDEX IF NOT EXISTS pkm_card_num ON pkm_card(number);
CREATE TABLE IF NOT EXISTS pkm_version (pid TEXT PRIMARY KEY, card_id TEXT, version TEXT);
CREATE INDEX IF NOT EXISTS pkm_version_card ON pkm_version(card_id);
CREATE TABLE IF NOT EXISTS pkm_guide (card_id TEXT, version TEXT, day TEXT, usd REAL, PRIMARY KEY (card_id, version, day));
CREATE TABLE IF NOT EXISTS pkm_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def build(raw: Path = RAW, db: Path = DB, rules: dict | None = None) -> int:
    """(Re)build the local index from the raw pages. Guide prices are added for the day of the pages, never replaced."""
    rules = rules if rules is not None else _rules()
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    sets = json.loads(gzip.decompress((raw / "sets.json.gz").read_bytes()))["data"]
    cards, guide = [], []
    for f in sorted(raw.glob("set-*.json.gz")) + sorted(raw.glob("cards-*.json.gz")):
        for c in json.loads(gzip.decompress(f.read_bytes()))["data"]:
            cards.append(c)
    with con:
        con.execute("DELETE FROM pkm_set")
        con.execute("DELETE FROM pkm_card")
        con.execute("DELETE FROM pkm_version")
        con.executemany("INSERT INTO pkm_set VALUES (?,?,?,?,?,?,?,?,?)", [
            (s["id"], s["name"], s.get("series"), s.get("printedTotal"), s.get("total"), (s.get("releaseDate") or "").replace("/", "-"),
             s.get("ptcgoCode"), (s.get("images") or {}).get("logo"), (s.get("images") or {}).get("symbol")) for s in sets])
        rows, vers = [], []
        uniq = {c["id"]: c for c in cards}
        cards = list(uniq.values())
        for c in cards:
            st = c.get("set") or {}
            rows.append((c["id"], c["name"], norm(c["name"]), st.get("id"), c.get("number"), c.get("supertype"), c.get("rarity"),
                         c.get("artist"), c.get("regulationMark"), (st.get("releaseDate") or "").replace("/", "-"), json.dumps(c)))
            for v in versions_of(c, rules):
                vers.append((printing_id(c["id"], v), c["id"], v))
            tp = c.get("tcgplayer") or {}
            day = (tp.get("updatedAt") or "")[:10].replace("/", "-")
            for k, p in (tp.get("prices") or {}).items():
                if k in PRICE_KEYS and day and (p or {}).get("market"):
                    guide.append((c["id"], PRICE_KEYS[k], day, float(p["market"])))
        con.executemany("INSERT OR REPLACE INTO pkm_card VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT OR REPLACE INTO pkm_version VALUES (?,?,?)", vers)
        con.executemany("INSERT OR IGNORE INTO pkm_guide VALUES (?,?,?,?)", guide)
        con.execute("INSERT OR REPLACE INTO pkm_meta VALUES ('built', ?)", (datetime.now(timezone.utc).isoformat(),))
    con.close()
    return len(cards)


# -- the catalog the engine and the UI use -------------------------------------------------------------------------------

class PkmCatalog:
    """Same shape the shared pricing code expects (printings, by_id, families, edition_rules), plus Pokémon lookups."""
    game = "pkm"

    def __init__(self, db: Path | str = DB):
        self.db_path = str(db)
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.executescript(_SCHEMA)
        self.edition_rules: dict = {}
        self._sets = {r[0]: {"id": r[0], "name": r[1], "series": r[2], "printed_total": r[3], "total": r[4], "release": r[5],
                             "ptcgo": r[6], "logo": r[7], "symbol": r[8]}
                      for r in self.con.execute("SELECT * FROM pkm_set")}
        self._cards: dict[str, tuple] = {r[0]: r for r in self.con.execute(
            "SELECT id, name, norm, set_id, number, supertype, rarity, artist, regmark, release FROM pkm_card")}
        self.printings: list[Printing] = []
        self.by_id: dict[str, Printing] = {}
        self.by_card: dict[str, list[Printing]] = {}
        for pid, cid, ver in self.con.execute("SELECT pid, card_id, version FROM pkm_version"):
            p = self._printing(pid, cid, ver)
            if p:
                self.printings.append(p)
                self.by_id[pid] = p
                self.by_card.setdefault(cid, []).append(p)
        for v in self.by_card.values():
            v.sort(key=lambda p: ORDER.index(p.edition) if p.edition in ORDER else 99)
        self.families = {f"pkf:{cid}": CardFamily(f"pkf:{cid}", {"en": r[1]}) for cid, r in self._cards.items()}
        self.by_name: dict[str, list[str]] = {}
        for cid, r in self._cards.items():
            self.by_name.setdefault(r[2], []).append(cid)
        self.by_number: dict[str, list[str]] = {}
        for cid, r in self._cards.items():
            self.by_number.setdefault(_num(r[4]), []).append(cid)
        self.set_names = {norm(s["name"]): sid for sid, s in self._sets.items()}
        self.set_codes = {s["ptcgo"].upper(): sid for sid, s in self._sets.items() if s.get("ptcgo")}

    def _printing(self, pid: str, cid: str, ver: str) -> Printing | None:
        r = self._cards.get(cid)
        if not r:
            return None
        s = self._sets.get(r[3]) or {}
        total = s.get("printed_total")
        return Printing(pid, f"pkf:{cid}", f"pka:{cid}", Region.EN_TCG, "en", r[3] or "", r[6] or "", set_code=f"{r[4]}/{total}" if total else r[4],
                        edition=ver, release_date=r[9], product_name=s.get("name"), source_refs=("pokemontcg.io",))

    def __len__(self) -> int:
        return len(self._cards)

    def set(self, sid: str) -> dict | None:
        return self._sets.get(sid)

    def card(self, card_id: str) -> dict | None:
        row = self.con.execute("SELECT json FROM pkm_card WHERE id=?", (card_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def versions(self, card_id: str) -> list[Printing]:
        return self.by_card.get(card_id, [])

    def card_name(self, card_id: str) -> str | None:
        r = self._cards.get(card_id)
        return r[1] if r else None

    def guide(self, pid: str) -> tuple[float, str] | None:
        """Latest TCGplayer market price for this version, in USD, with its day. A guide, never a sale."""
        p = self.by_id.get(pid)
        if not p:
            return None
        cid = pid.split(":")[2]
        row = self.con.execute("SELECT usd, day FROM pkm_guide WHERE card_id=? AND version=? ORDER BY day DESC LIMIT 1",
                               (cid, p.edition)).fetchone()
        return (row[0], row[1]) if row else None

    def guide_history(self, pid: str) -> list[tuple[str, float]]:
        p = self.by_id.get(pid)
        if not p:
            return []
        cid = pid.split(":")[2]
        return list(self.con.execute("SELECT day, usd FROM pkm_guide WHERE card_id=? AND version=? ORDER BY day", (cid, p.edition)))

    def search(self, q: str, limit: int = 48) -> list[dict]:
        """Name, set name or abbreviation, '199/165', '199', or a card id like sv3pt5-199."""
        qn = norm(q)
        if not qn:
            return []
        if q.strip() in self._cards:
            return [self._hit(q.strip(), 100)]
        m = re.search(r"(\d{1,3}[a-z]?)\s*/\s*(\d{1,3})", q, re.I)
        words = [w for w in qn.split() if w]
        set_hit = None
        for sname, sid in sorted(self.set_names.items(), key=lambda kv: -len(kv[0])):
            if sname and re.search(rf"(?<![a-z0-9]){re.escape(sname)}(?![a-z0-9])", qn):
                set_hit = sid
                break
        if not set_hit:
            for w in q.upper().split():
                if w in self.set_codes:
                    set_hit = self.set_codes[w]
        scored: dict[str, float] = {}
        if m:
            num, tot = m.group(1).lstrip("0").lower() or "0", int(m.group(2))
            for cid in self.by_number.get(num, []):
                s = self._sets.get(self._cards[cid][3]) or {}
                if s.get("printed_total") == tot:
                    scored[cid] = 80
        name_words = [w for w in words if not w.isdigit() and not re.fullmatch(r"\d+[a-z]?", w)]
        if set_hit:
            sw = set(norm(self._sets[set_hit]["name"]).split())
            name_words = [w for w in name_words if w not in sw and w.upper() not in self.set_codes]
        if name_words:
            phrase = " ".join(name_words)
            for cid, r in self._cards.items():
                n = r[2]
                if n == phrase:
                    scored[cid] = scored.get(cid, 0) + 60
                elif n.startswith(phrase):
                    scored[cid] = scored.get(cid, 0) + 45
                elif all(re.search(rf"(?<![a-z0-9]){re.escape(w)}", n) for w in name_words):
                    scored[cid] = scored.get(cid, 0) + 30
        elif set_hit and not m:
            for cid, r in self._cards.items():
                if r[3] == set_hit:
                    scored[cid] = 20
        if set_hit:
            in_set = {cid: s + 25 for cid, s in scored.items() if self._cards[cid][3] == set_hit}
            scored = in_set or scored                    # the set was named: only its cards, unless none match
        if m and name_words:
            scored = {cid: s for cid, s in scored.items() if s >= 80 or _num(self._cards[cid][4]) == (m.group(1).lstrip("0").lower() or "0")} or scored
        ranked = sorted((cid for cid, s in scored.items() if s > 0), key=lambda c: (-scored[c], self._cards[c][9] or "", c), reverse=False)
        ranked = sorted(ranked, key=lambda c: (-scored[c], -(int((self._cards[c][9] or "0000")[:4] or 0))))
        return [self._hit(cid, scored[cid]) for cid in ranked[:limit]]

    def _hit(self, cid: str, score: float) -> dict:
        r = self._cards[cid]
        s = self._sets.get(r[3]) or {}
        return {"card_id": cid, "key": card_key(cid), "name": r[1], "set": s.get("name"), "set_id": r[3], "number": r[4],
                "printed_total": s.get("printed_total"), "rarity": r[6], "release": r[9], "score": score}

    def image_url(self, card_id: str, size: str = "small") -> str | None:
        c = self.card(card_id)
        imgs = (c or {}).get("images") or {}
        return imgs.get("large" if size == "large" else "small")


def _num(n: str | None) -> str:
    return (n or "").lstrip("0").lower() or "0"


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Sync the Pokémon TCG catalog into data/cache/pkm.sqlite")
    ap.add_argument("--build-only", action="store_true", help="rebuild the index from pages already downloaded")
    a = ap.parse_args(argv)
    if not a.build_only:
        sync()
    print(f"indexed {build()} cards")


if __name__ == "__main__":
    main()
