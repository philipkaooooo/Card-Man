"""Every Pokémon that has a card, with its own icon and a log of each version's price.

A character is the Pokémon, not a print. Charizard ex and Charizard VSTAR are both Charizard and
are never one price. The log stores each version's own reading. A later day is added. An older day
is never rewritten and never borrowed by another version.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .character import species_of

ROOT = Path(__file__).resolve().parents[1]
SPECIES = ROOT / "data" / "pokemon_species.json"
DB = ROOT / "data" / "cache" / "characters.sqlite"
ICON = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{dex}.png"

_DISPLAY = {
    "mr-mime": "Mr. Mime", "mime-jr": "Mime Jr.", "mr-rime": "Mr. Rime",
    "ho-oh": "Ho-Oh", "porygon-z": "Porygon-Z",
    "jangmo-o": "Jangmo-o", "hakamo-o": "Hakamo-o", "kommo-o": "Kommo-o",
    "farfetchd": "Farfetch'd", "sirfetchd": "Sirfetch'd",
    "nidoran-f": "Nidoran♀", "nidoran-m": "Nidoran♂",
    "type-null": "Type: Null", "flabebe": "Flabébé",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS character (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, dex INTEGER NOT NULL, cards INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS character_card (
  slug TEXT NOT NULL, card_id TEXT NOT NULL, name TEXT NOT NULL, set_name TEXT, number TEXT,
  rarity TEXT, release TEXT, PRIMARY KEY (slug, card_id));
CREATE TABLE IF NOT EXISTS price_log (
  pid TEXT NOT NULL, day TEXT NOT NULL, usd REAL NOT NULL, source TEXT NOT NULL,
  slug TEXT NOT NULL, card_id TEXT NOT NULL, edition TEXT NOT NULL,
  PRIMARY KEY (pid, day, source, slug));
CREATE INDEX IF NOT EXISTS price_log_slug ON price_log(slug, card_id);
"""


def display_name(poke: str) -> str:
    return _DISPLAY.get(poke, poke.replace("-", " ").title())


def _key(text: str) -> str:
    t = (text or "").lower().replace("♀", " f").replace("♂", " m").replace("é", "e").replace("è", "e")
    t = t.replace("'", "").replace("’", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class _Book:
    def __init__(self, rows: list[dict]):
        self.by_key: dict[str, tuple[int, str, str]] = {}
        for r in rows:
            poke = r["name"]
            self.by_key[_key(poke.replace("-", " "))] = (int(r["dex"]), poke, display_name(poke))

    def one(self, species: str) -> tuple[int, str, str] | None:
        words = _key(species).split()
        for n in range(len(words), 0, -1):
            for i in range(len(words) - n + 1):
                hit = self.by_key.get(" ".join(words[i:i + n]))
                if hit:
                    return hit
        return None

    def of_card(self, name: str) -> list[tuple[int, str, str]]:
        out, seen = [], set()
        for species in species_of(name):
            hit = self.one(species)
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                out.append(hit)
        return out


def book() -> _Book:
    return _Book(json.loads(SPECIES.read_text()))


def _edition(pid: str) -> str:
    body = pid.split("@", 1)[0]
    return body.split(":")[-1].replace("-", " ")


def _card_id(pid: str) -> str:
    if "@pkm-" in pid:
        return pid.split("@pkm-", 1)[1]
    parts = pid.split(":")
    return parts[2] if len(parts) > 2 else pid


def open_db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(_SCHEMA)
    return con


def ensure(cat=None, log=None) -> None:
    """Fill the character list from the catalog, and append any price reading not already logged."""
    if not SPECIES.exists():
        return
    from .catalog.registry import DB as MASTER
    from .pkm_catalog import DB as PKM
    if cat is None:
        if not PKM.exists():
            return
        from .pkm_catalog import PkmCatalog
        cat = PkmCatalog(PKM)
    stamp = f"{SPECIES.stat().st_mtime_ns}:{PKM.stat().st_mtime_ns}:{MASTER.stat().st_mtime_ns if MASTER.exists() else 0}"
    con = open_db()
    have = con.execute("SELECT v FROM meta WHERE k='stamp'").fetchone()
    if have and have[0] == stamp:
        con.close()
        return
    who = book()
    cards: dict[str, list] = {}
    owners: dict[str, list[str]] = {}
    for cid, r in cat._cards.items():
        hits = who.of_card(r[1])
        s = cat.set(r[3]) or {}
        for _dex, poke, _name in hits:
            cards.setdefault(poke, []).append((cid, r[1], s.get("name") or "", r[4] or "", r[6] or "", r[9] or ""))
            owners.setdefault(cid, [])
            if poke not in owners[cid]:
                owners[cid].append(poke)
    fresh = []
    if MASTER.exists():
        src = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
        for pid, day, usd in src.execute(
                "SELECT pid, day, market FROM market_price WHERE source='tcgplayer' AND market IS NOT NULL AND pid LIKE 'pk:%'"):
            cid = _card_id(pid)
            for poke in owners.get(cid, ()):
                if day and usd is not None:
                    fresh.append((pid, day, float(usd), "tcgplayer", poke, cid, _edition(pid)))
        src.close()
    with con:
        con.execute("DELETE FROM character")
        con.execute("DELETE FROM character_card")
        for poke, rows in cards.items():
            _dex, _p, name = who.by_key[_key(poke.replace("-", " "))]
            con.execute("INSERT INTO character VALUES (?,?,?,?)", (poke, name, _dex, len(rows)))
            con.executemany("INSERT OR REPLACE INTO character_card VALUES (?,?,?,?,?,?,?)",
                            [(poke, cid, cname, sname, num, rarity, rel) for cid, cname, sname, num, rarity, rel in rows])
        con.executemany("INSERT OR IGNORE INTO price_log VALUES (?,?,?,?,?,?,?)", fresh)
        con.execute("INSERT OR REPLACE INTO meta VALUES ('stamp', ?)", (stamp,))
    con.close()
    if log:
        log(f"characters: {len(cards)} pokemon, {len(fresh)} price rows offered to the log")


def characters() -> list[tuple]:
    """(slug, name, dex, cards) in dex order. Empty until the catalog has been indexed."""
    if not DB.exists():
        return []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT slug, name, dex, cards FROM character ORDER BY dex").fetchall()
    con.close()
    return rows


def get(slug: str) -> tuple | None:
    if not DB.exists():
        return None
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT slug, name, dex, cards FROM character WHERE slug=?", (slug,)).fetchone()
    if row is None:
        row = con.execute(
            "SELECT slug, name, dex, cards FROM character WHERE replace(lower(name),' ','-')=? ORDER BY cards DESC LIMIT 1",
            (slug,)).fetchone()
    con.close()
    return row


def card_log(slug: str) -> list[dict]:
    """Every card of this character. Each version's readings stay on that version, oldest day first."""
    if not DB.exists():
        return []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cards = [{"id": cid, "name": name, "set": sname, "number": num, "rarity": rarity, "release": rel, "versions": {}}
             for cid, name, sname, num, rarity, rel in con.execute(
                 "SELECT card_id, name, set_name, number, rarity, release FROM character_card WHERE slug=? "
                 "ORDER BY release DESC, name, number", (slug,))]
    by = {c["id"]: c for c in cards}
    for cid, edition, day, usd in con.execute(
            "SELECT card_id, edition, day, usd FROM price_log WHERE slug=? ORDER BY card_id, edition, day", (slug,)):
        card = by.get(cid)
        if card is None:
            continue
        card["versions"].setdefault(edition, []).append((day, usd))
    con.close()
    return cards


def trend(cards: list[dict]) -> dict:
    """How this character's own versions moved. A version with one reading has no move. Nothing is averaged."""
    up = down = flat = unread = 0
    moves = []
    one_day = None
    for c in cards:
        for edition, pts in c["versions"].items():
            if len(pts) < 2:
                unread += 1
                if pts:
                    one_day = pts[-1][0]
                continue
            a, b = pts[0], pts[-1]
            one_day = b[0]
            if b[1] > a[1]:
                up += 1
                kind = "up"
            elif b[1] < a[1]:
                down += 1
                kind = "down"
            else:
                flat += 1
                kind = "flat"
            moves.append({"card": c["name"], "set": c["set"], "number": c["number"], "edition": edition,
                          "kind": kind, "start_day": a[0], "start": a[1], "end_day": b[0], "end": b[1]})
    moves.sort(key=lambda m: abs(m["end"] - m["start"]), reverse=True)
    return {"up": up, "down": down, "flat": flat, "unread": unread, "day": one_day, "moves": moves}


def icon(dex: int) -> tuple[bytes, str]:
    """The official artwork of this Pokémon, cached after the first fetch. A number if the file is missing."""
    path = ROOT / "data" / "cache" / "icons" / f"{dex}.png"
    if path.exists() and path.stat().st_size > 80:
        return path.read_bytes(), "image/png"
    import urllib.request
    try:
        req = urllib.request.Request(ICON.format(dex=dex), headers={"User-Agent": "cardman"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, "image/png"
    except Exception:
        pass
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" rx="16" fill="#f2f2f2"/>'
           f'<text x="48" y="56" text-anchor="middle" font-size="22" font-family="sans-serif">{dex}</text></svg>')
    return svg.encode(), "image/svg+xml"
