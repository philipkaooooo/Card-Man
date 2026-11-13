"""Card facts, search and images, keyed by passcode so a result is always one exact card.

Facts come from the local YAML Yugi checkout (names in every language, text, stats, sets, image file names) and are
indexed into data/cache/cardinfo.sqlite, rebuilt only when the checkout's revision changes. Search can be loose (English,
Japanese, reading, romaji, set code, passcode, typos), but every hit is one passcode and every printing shown is from the
identity catalog.

Images are downloaded once and served from this machine (the page's CSP allows only its own images):
* card artwork for a passcode, from YGOPRODeck (they ask for images to be downloaded, not hotlinked);
* a scan of a specific printing, from Yugipedia, only when its file name names that printing's set, region and rarity.
  Anything else is labelled as the card's artwork, never as "this printing".
"""
from __future__ import annotations

import difflib
import math
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARDS_DIR = ROOT / "data" / "cache" / "yaml-yugi" / "data" / "cards"
DB = ROOT / "data" / "cache" / "cardinfo.sqlite"
IMAGES = ROOT / "data" / "cache" / "images"
UA = "cardman/0.2 (private research tool)"
INDEX_VERSION = "cardinfo-1"
ARTWORK_URL = "https://images.ygoprodeck.com/images/cards{size}/{passcode}.jpg"
SCAN_URL = "https://ms.yugipedia.com//thumb/{a}/{ab}/{name}/400px-{name}"
MISS_RETRY_S = 24 * 3600

# Rarity names in the catalog -> the abbreviation Yugipedia uses in scan file names.
RARITY_CODE = {
    "Common": "C", "Rare": "R", "Super Rare": "SR", "Ultra Rare": "UR", "Secret Rare": "ScR", "Ultimate Rare": "UtR",
    "Ghost Rare": "GR", "Holographic Rare": "HGR", "Prismatic Secret Rare": "PScR", "Platinum Secret Rare": "PlScR",
    "Quarter Century Secret Rare": "QCScR", "Collector's Rare": "CR", "Gold Rare": "GUR", "Gold Secret Rare": "GScR",
    "Premium Gold Rare": "PGR", "Starlight Rare": "StR", "Short Print": "SP", "Super Short Print": "SSP",
    "Normal Parallel Rare": "NPR", "Super Parallel Rare": "SPR", "Ultra Parallel Rare": "UPR", "Normal Rare": "NR",
    "Duel Terminal Normal Parallel Rare": "DNPR", "Duel Terminal Super Parallel Rare": "DSPR", "Shatterfoil Rare": "SHR",
    "Starfoil Rare": "SFR", "Mosaic Rare": "MSR", "Millennium Rare": "MLR", "Platinum Rare": "PlR",
    "20th Secret Rare": "20ScR", "Extra Secret Rare": "EScR",
}
EDITION_CODE = {"1st": "1E", "Unlimited": "UE", "Limited": "LE"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS card (passcode TEXT PRIMARY KEY, konami_id INTEGER, data_json TEXT NOT NULL);
"""


def strip_ruby(s: str | None) -> tuple[str | None, str | None]:
    if not s or "<ruby>" not in s:
        return s, None
    base = re.sub(r"<rt>.*?</rt>", "", s).replace("<ruby>", "").replace("</ruby>", "")
    return base, re.sub(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", r"\2", s)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _card_record(d: dict) -> dict:
    names, text = d.get("name") or {}, d.get("text") or {}
    ja, reading = strip_ruby(names.get("ja"))
    ja_text, _ = strip_ruby(text.get("ja"))
    pend = d.get("pendulum_effect") or {}
    return {
        "passcode": str(d["password"]), "konami_id": d.get("konami_id"), "name_en": names.get("en"), "name_ja": ja,
        "reading": reading, "romaji": names.get("ja_romaji"), "names": {k: strip_ruby(v)[0] for k, v in names.items()
                                                                         if k not in ("ja_romaji", "ko_rr")},
        "card_type": d.get("card_type"), "type_line": d.get("monster_type_line"), "attribute": d.get("attribute"),
        "property": d.get("property"), "level": d.get("level"), "rank": d.get("rank"), "link_arrows": d.get("link_arrows"),
        "atk": d.get("atk"), "def": d.get("def"), "scale": d.get("pendulum_scale"), "materials": d.get("materials"),
        "text_en": text.get("en"), "text_ja": ja_text, "pend_en": pend.get("en"), "pend_ja": strip_ruby(pend.get("ja"))[0],
        "limits": d.get("limit_regulation") or {}, "series": d.get("series") or [], "images": d.get("images") or [],
        "yugipedia_page_id": d.get("yugipedia_page_id"), "fake_password": bool(d.get("fake_password")),
        "unofficial_translation": bool(d.get("is_translation_unofficial")),
    }


def _revision(cards_dir: Path) -> str:
    head = cards_dir.parent.parent / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            ref = (cards_dir.parent.parent / ".git" / ref.split(" ", 1)[1]).read_text().strip()
        return ref
    except OSError:
        files = sorted(cards_dir.glob("*.json"))
        return hashlib.sha256("".join(f"{f.name}{f.stat().st_mtime_ns}" for f in files).encode()).hexdigest()[:16]


@dataclass
class Hit:
    passcode: str
    name: str
    name_ja: str | None
    matched: str
    score: float


class CardIndex:
    def __init__(self, cards_dir: Path | str = CARDS_DIR, db_path: Path | str = DB, catalog=None):
        self.cards_dir, self.db_path = Path(cards_dir), Path(db_path)
        self.catalog = catalog      # zero-argument callable returning the identity Catalog, or a Catalog, or None
        self._lock = threading.Lock()
        self._loaded = False

    # -- building -------------------------------------------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        db.executescript(_SCHEMA)
        return db

    def ensure(self) -> None:
        with self._lock:
            if self._loaded:
                return
            db = self._db()
            rev = f"{INDEX_VERSION}:{_revision(self.cards_dir)}"
            row = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
            if not row or row[0] != rev:
                rows = []
                for f in sorted(self.cards_dir.glob("*.json")):
                    try:
                        d = json.loads(f.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if d.get("password") is None:
                        continue
                    r = _card_record(d)
                    rows.append((r["passcode"], r["konami_id"], json.dumps(r, ensure_ascii=False)))
                with db:
                    db.execute("DELETE FROM card")
                    db.executemany("INSERT OR REPLACE INTO card VALUES (?,?,?)", rows)
                    db.execute("INSERT OR REPLACE INTO meta VALUES ('revision', ?)", (rev,))
            self.cards = {p: json.loads(j) for p, j in db.execute("SELECT passcode, data_json FROM card")}
            db.close()
            self._build_search()
            self._loaded = True

    def _cat(self):
        c = self.catalog
        return c() if callable(c) else c

    def _build_search(self) -> None:
        keys: list[tuple[str, str, str]] = []          # (normalized key, passcode, what matched)
        for p, c in self.cards.items():
            for label, v in (("name", c["name_en"]), ("Japanese name", c["name_ja"]), ("reading", c["reading"]),
                             ("romaji", c["romaji"])):
                if v:
                    keys.append((norm(v), p, label))
        from .identity import load_name_aliases
        by_en = {norm(c["name_en"]): p for p, c in self.cards.items() if c["name_en"]}
        for printed, official in load_name_aliases().items():
            if norm(official) in by_en:
                keys.append((norm(printed), by_en[norm(official)], "printed name"))
        self._keys = keys
        self._by_code: dict[str, set[str]] = {}
        self._prints: dict[str, list] = {}
        cat = self._cat()
        if cat is not None:
            for pr in cat.printings:
                pc = pr.artwork_id.removeprefix("art:pw")
                self._prints.setdefault(pc, []).append(pr)
                if pr.set_code:
                    self._by_code.setdefault(pr.set_code.upper(), set()).add(pc)
        self._en_names = sorted({k for k, _, label in keys if label == "name"})

    # -- reading --------------------------------------------------------------------------------------------------
    def card(self, passcode: str) -> dict | None:
        self.ensure()
        return self.cards.get(str(passcode))

    def printings(self, passcode: str) -> list:
        self.ensure()
        order = {"EN_TCG": 0, "JP_OCG": 1}
        return sorted(self._prints.get(str(passcode), []), key=lambda p: (order.get(p.region.value, 2), p.set_code or "~", p.rarity))

    def search(self, q: str, limit: int = 24) -> list[Hit]:
        self.ensure()
        q = (q or "").strip()
        if not q:
            return []
        hits: dict[str, Hit] = {}

        def add(p, score, matched):
            c = self.cards.get(p)
            # Among equally good text matches, the card printed most often is the one people mean ("blue-eyes" ->
            # Blue-Eyes White Dragon, not Blue-Eyes Jet Dragon). Never enough to beat a better text match.
            score += min(6.0, math.log2(1 + len(self._prints.get(p, ())))) if score < 90 else 0
            if c and (p not in hits or hits[p].score < score):
                hits[p] = Hit(p, c["name_en"] or c["name_ja"] or p, c["name_ja"], matched, score)

        up = unicodedata.normalize("NFKC", q).upper().replace(" ", "")
        if up.isdigit() and up in self.cards:
            add(up, 100, "passcode")
        for code in (up, up.replace("_", "-")):
            for p in self._by_code.get(code, ()):
                add(p, 95, f"set code {code}")
        if "-" in up and len(up) >= 4 and not hits:
            for code, ps in self._by_code.items():
                if code.startswith(up):
                    for p in ps:
                        add(p, 70, f"set code {code}")
        nq = norm(q)
        if nq:
            for key, p, label in self._keys:
                if key == nq:
                    add(p, 90, label)
                elif key.startswith(nq):
                    add(p, 80 - min(10, (len(key) - len(nq)) / 4), label)
                elif f" {nq}" in f" {key}":
                    add(p, 65 - min(10, (len(key) - len(nq)) / 6), label)
                elif len(nq) >= 3 and nq in key:
                    add(p, 50, label)
            if not hits and len(nq) >= 4:
                for name in difflib.get_close_matches(nq, self._en_names, n=8, cutoff=0.72):
                    for key, p, label in self._keys:
                        if key == name and label == "name":
                            add(p, 40 * difflib.SequenceMatcher(None, nq, name).ratio(), "close spelling")
        return sorted(hits.values(), key=lambda h: (-h.score, h.name))[:limit]

    def suggest(self, q: str, limit: int = 8) -> list[dict]:
        hits = self.search(q, limit)
        names = [h.name for h in hits]
        return [{"passcode": h.passcode, "name": h.name, "ja": h.name_ja,
                 "matched": (f"artwork {h.passcode}" if names.count(h.name) > 1 else h.matched),
                 "type": (self.cards[h.passcode].get("type_line") or self.cards[h.passcode].get("card_type"))}
                for h in hits]

    # -- matching a printing to a scan ----------------------------------------------------------------------------
    def printing_scan(self, passcode: str, printing) -> str | None:
        """The Yugipedia scan file for exactly this printing (set prefix + region + rarity, and edition when known),
        or None. Never a near match."""
        c = self.card(passcode)
        if not c or not printing.set_code:
            return None
        prefix = printing.set_code.split("-")[0].upper()
        region = {"EN_TCG": "EN", "JP_OCG": "JP"}.get(printing.region.value)
        code = RARITY_CODE.get(printing.rarity)
        if not (region and code):
            return None
        want_ed = EDITION_CODE.get(printing.edition or "")
        found = []
        for im in c["images"]:
            name = im.get("image") or ""
            parts = name.rsplit(".", 1)[0].split("-")
            if len(parts) >= 4 and parts[1].upper() == prefix and parts[2] == region and parts[3] == code:
                ed = parts[4] if len(parts) >= 5 and parts[4] in EDITION_CODE.values() else None
                if want_ed and ed and ed != want_ed:
                    continue
                found.append(name)
        return found[0] if found else None

    def known_scan(self, filename: str) -> bool:
        self.ensure()
        if not hasattr(self, "_scans"):
            self._scans = {im.get("image") for c in self.cards.values() for im in c["images"] if im.get("image")}
        return filename in self._scans


# -- image cache ------------------------------------------------------------------------------------------------------

PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 421 614"><rect width="421" height="614" rx="18" fill="#e9e7e1"/>'
               '<rect x="28" y="28" width="365" height="558" rx="10" fill="none" stroke="#c9c6bd" stroke-width="3"/>'
               '<text x="210" y="300" font-family="system-ui,sans-serif" font-size="26" fill="#8e8a80" text-anchor="middle">No image</text>'
               '<text x="210" y="336" font-family="system-ui,sans-serif" font-size="18" fill="#a6a297" text-anchor="middle">yet for this card</text></svg>').encode()

_fetch_gate = threading.BoundedSemaphore(4)


def _http_get(url: str) -> tuple[bytes, str]:
    with _fetch_gate:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(), r.headers.get("Content-Type") or ""


class ImageCache:
    def __init__(self, root: Path | str = IMAGES, get=_http_get):
        self.root, self.get = Path(root), get

    def _cached(self, path: Path, url: str, kinds: tuple[str, ...]) -> tuple[bytes, str] | None:
        miss = path.with_suffix(path.suffix + ".miss")
        if path.exists():
            return path.read_bytes(), kinds[0]
        if miss.exists() and time.time() - miss.stat().st_mtime < MISS_RETRY_S:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            body, ctype = self.get(url)
        except Exception:
            miss.touch()
            return None
        if not body or not any(k.split("/")[1] in ctype for k in kinds):
            miss.touch()
            return None
        # A private temp file per writer: two requests for the same uncached image must not share one.
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return body, kinds[0]

    def artwork(self, passcode: str, size: str = "") -> tuple[bytes, str]:
        """size: '' full card, 'small', 'cropped' (art only)."""
        if not re.fullmatch(r"\d{1,10}", passcode) or size not in ("", "small", "cropped"):
            return PLACEHOLDER, "image/svg+xml"
        path = self.root / "artwork" / (size or "full") / f"{passcode}.jpg"
        got = self._cached(path, ARTWORK_URL.format(size=f"_{size}" if size else "", passcode=passcode), ("image/jpeg",))
        return got or (PLACEHOLDER, "image/svg+xml")

    def pkm(self, card_id: str, url: str | None, size: str = "small") -> tuple[bytes, str]:
        """A Pokémon card picture from images.pokemontcg.io, cached like the Yu-Gi-Oh! artwork."""
        if not re.fullmatch(r"[a-z0-9.]{2,16}-[A-Za-z0-9]{1,8}", card_id or "") or size not in ("small", "large") \
                or not (url or "").startswith("https://images.pokemontcg.io/"):
            return PLACEHOLDER, "image/svg+xml"
        path = self.root / "pkm" / size / f"{card_id}.png"
        got = self._cached(path, url, ("image/png",))
        return got or (PLACEHOLDER, "image/svg+xml")

    def scan(self, filename: str) -> tuple[bytes, str]:
        if not re.fullmatch(r"[A-Za-z0-9!'(),._&+-]{3,200}\.(png|jpg)", filename):
            return PLACEHOLDER, "image/svg+xml"
        h = hashlib.md5(filename.encode()).hexdigest()
        path = self.root / "scans" / filename
        ext = "image/png" if filename.endswith(".png") else "image/jpeg"
        got = self._cached(path, SCAN_URL.format(a=h[0], ab=h[:2], name=filename), (ext,))
        return got or (PLACEHOLDER, "image/svg+xml")
