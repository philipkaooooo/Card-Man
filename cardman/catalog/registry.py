"""Build the master card list (data/cache/catalog.sqlite) from our catalogs and TCGplayer's (via TCGCSV).

Exact rules only. Anything that does not match exactly goes to `needs_look` with the reason; nothing is guessed.
  set      TCGplayer group -> our set: same set code (abbreviation), or the same name, or the same release date with
           one name inside the other. More than one candidate is not a match.
  card     Pokémon: same number (left of '/') and the same name, in the matched set.
           Yu-Gi-Oh!: same printed code (LOB-EN001) and the same rarity (exact wording table); TCGplayer's
           set groups are only recorded, since they bundle or rename sets.
  version  TCGplayer finish/edition (Normal, Holofoil, Reverse Holofoil, 1st Edition, Unlimited...) -> our version.
           A version TCGplayer sells that our list lacks is added, marked as coming from TCGplayer.
  reprint  Pokémon Classic Collection cards print the original card's number ("Shining Celebi 106/105"): the original
           is the card with that name, number and set size released before it. Yu-Gi-Oh!: a later printing of the
           same artwork is a reprint of the earliest one. Hand-checked exceptions: data/catalog_overrides.csv.
  missing  A set or card a source lists that we do not have. Shown in Settings and the engine log; a test keeps it at 0
           for the recorded snapshot.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "cache" / "catalog.sqlite"
OVERRIDES = ROOT / "data" / "catalog_overrides.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS printing (pid TEXT PRIMARY KEY, game TEXT, set_id TEXT, set_name TEXT, release TEXT, number TEXT, name TEXT,
  rarity TEXT, variant TEXT, language TEXT, subset TEXT, is_reprint INTEGER DEFAULT 0, reprint_of TEXT, sources TEXT);
CREATE INDEX IF NOT EXISTS printing_game ON printing(game, set_id);
CREATE INDEX IF NOT EXISTS printing_reprint ON printing(reprint_of);
CREATE TABLE IF NOT EXISTS xref (pid TEXT, source TEXT, ext_id TEXT, ext_variant TEXT, PRIMARY KEY (source, ext_id, ext_variant));
CREATE INDEX IF NOT EXISTS xref_pid ON xref(pid);
CREATE TABLE IF NOT EXISTS source_set (source TEXT, game TEXT, ext_id TEXT, name TEXT, code TEXT, release TEXT, our_set TEXT,
  first_seen TEXT, last_seen TEXT, PRIMARY KEY (source, game, ext_id));
CREATE TABLE IF NOT EXISTS needs_look (source TEXT, game TEXT, ext_id TEXT, kind TEXT, detail TEXT, seen TEXT,
  PRIMARY KEY (source, game, ext_id, kind));
CREATE TABLE IF NOT EXISTS market_price (pid TEXT, source TEXT, day TEXT, low REAL, mid REAL, market REAL, PRIMARY KEY (pid, source, day));
CREATE TABLE IF NOT EXISTS catalog_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS extra_card (cid TEXT PRIMARY KEY, set_id TEXT, set_name TEXT, release TEXT, number TEXT, name TEXT, rarity TEXT,
  image TEXT, subset TEXT, data_json TEXT);
CREATE TABLE IF NOT EXISTS extra_set (set_id TEXT PRIMARY KEY, name TEXT, release TEXT);
CREATE TABLE IF NOT EXISTS dropped (pid TEXT PRIMARY KEY, why TEXT);
"""

PKM_FINISH = {"normal": "Normal", "holofoil": "Holo", "reverse holofoil": "Reverse Holo", "1st edition holofoil": "1st Edition Holo",
              "1st edition": "1st Edition", "1st edition normal": "1st Edition", "unlimited holofoil": "Unlimited Holo",
              "unlimited": "Unlimited"}
YGO_EDITION = {"1st edition": "1st", "unlimited": "Unlimited", "limited": "Limited"}
REPRINT_SETS = re.compile(r"classic collection", re.I)
_KIND = re.compile(r"\b(promos?|energ(?:y|ies)|trainer kit|training kit|deck|jumbo|collection|gallery|subset|classic|"
                   r"shiny vault|galarian gallery|trainer gallery|radiant|league|prize|sample|tins?|box)\b", re.I)


def _kind_words(name: str) -> set[str]:
    return {re.sub(r"s$|ies$", "", w.lower()) for w in _KIND.findall(name or "")}


GENERIC_CODES = {"PR", "PROMO", "SAMPLE", "MCAP"}       # TCGplayer gives these to many unrelated groups


def norm(s: str | None) -> str:
    s = re.sub(r"['’`´]", "", s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _cname(name: str | None) -> str:
    """A Pokémon card name for matching across sources: the same name however each one spells it."""
    n = (name or "").replace("♀", " F").replace("♂", " M")
    n = norm(re.sub(r"[★☆δ]", " ", n))                         # "Umbreon ★" = "Umbreon Star"; "Swablu δ" = "Swablu"
    n = re.sub(r"\b(?:star|delta species)$", "", n).strip()
    return re.sub(r"\bimposter\b", "impostor", n)


def _group_name(name: str) -> str:
    """'SV: Scarlet & Violet 151' -> 'scarlet and violet 151'; 'ME04: Chaos Rising' -> 'chaos rising'."""
    return norm(re.sub(r"^[A-Za-z]{1,5}\d{0,3}(?:\.\d)?[a-z]?:\s*", "", name or ""))


def _num(n: str | None) -> str:
    left = (n or "").split("/")[0].strip()
    return left.lstrip("0").lower() or "0"


ALIASES = ROOT / "data" / "catalog_set_aliases.csv"


def _aliases(game: str, path: Path = ALIASES) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["source_set"]: r["our_set"] for r in csv.DictReader(l for l in f if not l.startswith("#")) if r.get("game") == game}


def _split_name(name: str) -> tuple[str, str]:
    """'Murkrow - 021 (Prerelease) [Staff]' -> ('Murkrow', 'Prerelease Staff'); 'Charizard' -> ('Charizard', '')."""
    mods = " ".join(m.strip("()[] ") for m in re.findall(r"\([^)]*\)|\[[^\]]*\]", name or ""))
    base = re.sub(r"\s*[\(\[].*?[\)\]]", "", name or "")
    base = re.sub(r"\s+-\s+[A-Za-z]{0,5}\d+[a-z]?(?:/[A-Za-z0-9-]+)?\s*$", "", base).strip()
    base = re.sub(r"\s+[A-Za-z]{0,5}\d+[a-z]?/[A-Za-z]{0,5}\d+\s*$", "", base).strip()
    mods = re.sub(r"\b[A-Za-z]{0,4}\d+[a-z]?/\d+\b", "", mods).strip()     # a number inside brackets is not a stamp
    return base, re.sub(r"\s+", " ", mods)


def _overrides(path: Path = OVERRIDES) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["pid"]: r["reprint_of"] for r in csv.DictReader(f) if r.get("pid") and not r["pid"].startswith("#")}



# TCGplayer's rarity wording -> ours (normalised). Exact table; anything not listed must match by name.
_YGO_RARITY = {
    "common": {"common", "short print", "super short print"},
    "short print": {"short print", "common"},
    "prismatic collector's rare": {"collector's rare"},
    "prismatic ultimate rare": {"ultimate rare"},
    "duel terminal technology common": {"duel terminal normal parallel rare"},
    "duel terminal technology rare": {"duel terminal rare parallel rare"},
    "duel terminal technology super rare": {"duel terminal super parallel rare"},
    "duel terminal technology ultra rare": {"duel terminal ultra parallel rare"},
    "duel terminal normal parallel rare": {"duel terminal normal parallel rare"},
    "parallel rare": {"normal parallel rare", "parallel rare"},
    "ghost rare": {"ghost rare", "ghost/gold rare"},
    "gold rare": {"gold rare", "ghost/gold rare"},
    "ultra rare": {"ultra rare"},
    "ultra pharaoh’s rare": {"ultra rare (pharaoh's rare)"},
}


def _ygo_rarity(r: str) -> set[str]:
    n = norm(r)
    table = {norm(k): {norm(v) for v in vs} for k, vs in _YGO_RARITY.items()}
    return table.get(n, {n})


_RARITY_TAGS = re.compile(r"^(?:ur|sr|scr|pcr|pur|qcsr|psr|gr|ugr|str|cr|ghr|pgr|gscr|scr|sp|ssp|r|c|"
                          r"(?:prismatic |platinum |quarter century |gold |ultra |super |starlight |ghost |collector.?s |"
                          r"ultimate |premium gold |secret |duel terminal |pharaoh.?s )*(?:rare|secret rare)|"
                          r"duel terminal)$", re.I)


def _ygo_subversion(name: str, num: str, rar: str) -> str:
    """The part of a TCGplayer product name that marks a sub-version our list does not separate: '(Red)', '(8th Art)',
    '(Unlimited Misprint)'. Rarity tags and the printed code are not sub-versions."""
    tags = []
    for t in re.findall(r"\(([^)]*)\)", name or ""):
        t = t.strip()
        if not t or t.upper() == num or _RARITY_TAGS.match(t) or norm(t) == norm(rar) or re.fullmatch(r"[A-Z0-9]{2,5}-[A-Z]{0,2}\d+[A-Z]?", t):
            continue
        tags.append(t)
    return " ".join(tags)


_REPRINT_GROUP = re.compile(r"\s*\((20\d\d (?:date )?reprint|25th anniversary edition)\)\s*$", re.I)


def _reprint_group(gname: str, strip: bool = False) -> str:
    """TCGplayer sells a reprint wave as its own set: 'Retro Pack (2020 Date Reprint)'. Returns the wave's tag
    ('2020 Date Reprint'), or with strip=True the set name without it."""
    m = _REPRINT_GROUP.search(gname or "")
    if strip:
        return _REPRINT_GROUP.sub("", gname or "") if m and "25th" not in m.group(1).lower() else (gname or "")
    return m.group(1) if m and "25th" not in m.group(1).lower() else ""


def _same_set(hit: list, gname: str) -> list:
    """Of our printings with one code in several of our sets, the ones in the set TCGplayer names. Exact or
    containment only; '25th Anniversary' must agree both ways, so a reprint is never put on its original."""
    g = norm(gname)
    anniv = "25th anniversary" in g
    keep = [p for p in hit if ("25th anniversary" in norm(p.product_name)) == anniv]
    exact = [p for p in keep if norm(p.product_name) == g]
    if exact:
        keep = exact
    else:
        inside = [p for p in keep if norm(p.product_name) in g or g in norm(p.product_name)]
        keep = inside or keep
    return keep if len({p.product_name for p in keep}) == 1 else []


def _ygo_set_dates(root: Path | None = None) -> dict[str, str]:
    """Set name -> first TCG release date, from YGOPRODeck's set list (our printings carry no dates)."""
    import html
    f = (root or SETLISTS) / "ygoprodeck_cardsets.json"
    if not f.exists():
        return {}
    return {norm(html.unescape(x["set_name"])): x["tcg_date"] for x in json.loads(f.read_text()) if x.get("tcg_date")}


def _family_name(ycat, p) -> str:
    fam = ycat.families.get(p.family_id)
    return (fam.names.get("en") or "") if fam else ""


class Builder:
    def __init__(self, db: Path | str = DB):
        self.con = sqlite3.connect(db)
        self.con.executescript(SCHEMA)
        self.now = datetime.now(timezone.utc).date().isoformat()
        self.look: list[tuple] = []
        self.stats: dict[str, int] = {}

    def _reset(self, game: str, tcg) -> None:
        """A rebuild replaces a game's links and today's prices completely, so a link from an earlier rule never lingers."""
        old = "SELECT pid FROM printing WHERE game=?"
        self.con.execute(f"DELETE FROM xref WHERE pid IN ({old})", (game,))
        if tcg is not None:
            self.con.execute(f"DELETE FROM market_price WHERE day=? AND pid IN ({old})", (tcg.parent.name, game))

    def _bump(self, k: str, n: int = 1) -> None:
        self.stats[k] = self.stats.get(k, 0) + n

    def _source_set(self, source, game, ext_id, name, code, release, our):
        self.con.execute("INSERT INTO source_set VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(source, game, ext_id) DO UPDATE SET "
                         "name=excluded.name, code=excluded.code, release=excluded.release, our_set=excluded.our_set, last_seen=excluded.last_seen",
                         (source, game, str(ext_id), name, code, release, our, self.now, self.now))

    def _need(self, source, game, ext_id, kind, detail):
        self.look.append((source, game, str(ext_id), kind, detail, self.now))

    # -- Pokémon ----------------------------------------------------------------------------------------------------
    def pokemon(self, pcat, tcg=None) -> None:
        self._reset("pkm", tcg)
        claimed: dict[str, int] = {}
        self.con.execute("DELETE FROM dropped WHERE pid LIKE 'pk:%'")
        rows = {}
        sets = pcat._sets
        for pid, p in pcat.by_id.items():
            cid = pid.split(":")[2]
            c = pcat._cards.get(cid)
            if not c or cid.startswith("tcg-"):
                continue
            s = sets.get(c[3]) or {}
            rows[pid] = [pid, "pkm", c[3], s.get("name"), s.get("release"), c[4], c[1], c[6], p.edition, "en",
                         "Classic Collection" if REPRINT_SETS.search(s.get("name") or "") else None, 0, None, ["pokemontcg.io"]]
        extra: dict[str, tuple] = {}
        if tcg is not None:
            from ..pkm_catalog import printing_id
            from .tcgcsv import load
            groups, products, prices = load(tcg)
            aliases = _aliases("pkm")
            set_by_code, set_by_name, cards_in = {}, {}, {}
            for sid, s in sets.items():
                if s.get("ptcgo"):
                    set_by_code.setdefault(s["ptcgo"].upper(), []).append(sid)
                set_by_name.setdefault(norm(s["name"]), []).append(sid)
            for cid, c in pcat._cards.items():
                if not cid.startswith("tcg-"):
                    cards_in.setdefault(c[3], []).append(cid)
            for g in groups:
                gid, gname = g["groupId"], g["name"]
                code, rel = (g.get("abbreviation") or "").upper(), (g.get("publishedOn") or "")[:10]
                pool: list[str] = []                        # an alias may name several of our sets (two decks sold as one kit)
                if gname in aliases:
                    pool = [x for x in aliases[gname].split("|") if x in sets]
                    cands = pool[:1]
                else:
                    cands = set_by_name.get(_group_name(gname)) or set_by_name.get(norm(gname)) or []
                    if not cands and code and code not in GENERIC_CODES and code in set_by_code:
                        # a code counts only for a set of the same year: TCGplayer reuses codes across years
                        cands = [s for s in set_by_code[code] if (sets[s].get("release") or "")[:4] == rel[:4]]
                    if not cands:
                        gn = _group_name(gname)
                        cands = [sid for sid, s in sets.items() if s.get("release") == rel and gn and
                                 (norm(s["name"]) in gn or gn in norm(s["name"]))]
                    # "ME: Mega Evolution Promo" is not "Mega Evolution", and "BW Trainer Kit" is not "Black & White":
                    # a product word in one name and not the other means different sets.
                    cands = [c for c in cands if _kind_words(gname) == _kind_words(sets[c]["name"])]
                cands = sorted(set(cands))
                our = cands[0] if len(cands) == 1 else None
                self._source_set("tcgplayer", "pkm", gid, gname, code, rel, our or f"tcg-g{gid}")
                if len(cands) > 1:
                    self._need("tcgplayer", "pkm", gid, "set", f"{gname} ({code}, {rel}): several of our sets fit: {', '.join(cands)}")
                self._bump("pkm_sets_matched" if our else "pkm_sets_added")
                jumbo = "jumbo" in gname.lower()
                price_of = {(x["productId"], x["subTypeName"]): x for x in prices.get(gid, [])}
                finishes: dict[int, list] = {}
                for x in prices.get(gid, []):
                    finishes.setdefault(x["productId"], []).append(x["subTypeName"])
                for prod in products.get(gid, []):
                    ext = {e["name"]: e["value"] for e in prod.get("extendedData") or []}
                    m = re.search(r"([A-Za-z]{0,5}\d+[a-z]?)/([A-Za-z]{0,5}\d+)", prod.get("name") or "")
                    number_full = ext.get("Number") or (m.group(0) if m else None)
                    if not number_full:
                        continue                                    # sealed product, code cards, accessories
                    base, mods = _split_name(prod["name"])
                    hit = [cid for sid in (pool or [our]) for cid in cards_in.get(sid, [])
                           if _num(pcat._cards[cid][4]) == _num(number_full) and _cname(pcat._cards[cid][1]) == _cname(base)] if our else []
                    if len(hit) > 1:
                        self._need("tcgplayer", "pkm", prod["productId"], "card", f"{prod['name']} #{number_full} in {gname}: several of our cards fit")
                        continue
                    if hit:
                        cid = hit[0]
                    else:
                        cid = f"tcg-{prod['productId']}"            # a card our main source lacks: added from TCGplayer
                        extra[cid] = (cid, our or f"tcg-g{gid}", sets.get(our, {}).get("name") or gname, rel, number_full, base or prod["name"],
                                      ext.get("Rarity"), prod.get("imageUrl"), "Jumbo" if jumbo else None,
                                      json.dumps({k: ext.get(k) for k in ("Card Type", "HP", "Stage", "CardText", "Weakness", "Resistance",
                                                                          "RetreatCost", "Attack 1", "Attack 2") if ext.get(k)}))
                        self._bump("pkm_cards_added")
                    sold = {PKM_FINISH.get(f.lower()) for f in finishes.get(prod["productId"], [])} - {None}
                    if hit and sold and not mods and (pcat.card(cid) or {}).get("_versions_inferred"):
                        # Our source had no versions for this card and we inferred them; TCGplayer says which exist.
                        for p in pcat.versions(cid):
                            if p.edition not in sold and not (p.edition == "Unlimited Holo" and "Holo" in sold) and " · " not in (p.edition or ""):
                                rows.pop(p.id, None)
                                self.con.execute("INSERT OR REPLACE INTO dropped VALUES (?, ?)", (p.id, "TCGplayer does not sell this version"))
                                self._bump("pkm_inferred_versions_dropped")
                    for fin in finishes.get(prod["productId"], []) or ["Normal"]:
                        ver = PKM_FINISH.get(fin.lower())
                        if not ver:
                            self._need("tcgplayer", "pkm", f"{prod['productId']}:{fin}", "version", f"{prod['name']}: finish {fin}")
                            continue
                        if "(shadowless)" in gname.lower() and not ver.startswith("1st"):
                            ver = "Shadowless"                      # that group's "Unlimited" copies are the Shadowless print
                        if mods:
                            ver = f"{ver} · {mods}"                 # a stamped or special print of the card: its own version
                        mine = [p for p in pcat.versions(cid) if p.edition == ver or (ver == "Holo" and p.edition == "Unlimited Holo")
                                or (ver == "Normal" and p.edition == "Unlimited")] if not cid.startswith("tcg-") else []
                        pid = mine[0].id if mine else printing_id(cid, ver)
                        if pid not in rows:
                            c = pcat._cards.get(cid)
                            e_ = extra.get(cid)
                            rows[pid] = [pid, "pkm", (c[3] if c else e_[1]), (sets.get(c[3], {}).get("name") if c else e_[2]),
                                         (sets.get(c[3], {}).get("release") if c else e_[3]), (c[4] if c else e_[4]), (c[1] if c else e_[5]),
                                         (c[6] if c else e_[6]), ver, "en",
                                         "Jumbo" if jumbo else ("Classic Collection" if REPRINT_SETS.search(gname) else None), 0, None, []]
                            self._bump("pkm_versions_added")
                        if claimed.setdefault(pid, prod["productId"]) != prod["productId"]:
                            self._need("tcgplayer", "pkm", f"{prod['productId']}:{fin}", "version",
                                       f"{prod['name']} #{number_full} {fin} in {gname}: another TCGplayer product is already this version")
                            continue
                        rows[pid][13].append("tcgplayer")
                        self.con.execute("INSERT OR REPLACE INTO xref VALUES (?,?,?,?)", (pid, "tcgplayer", str(prod["productId"]), fin))
                        pr = price_of.get((prod["productId"], fin))
                        if pr and pr.get("marketPrice") is not None:
                            self.con.execute("INSERT OR REPLACE INTO market_price VALUES (?,?,?,?,?,?)",
                                             (pid, "tcgplayer", tcg.parent.name, pr.get("lowPrice"), pr.get("midPrice"), pr.get("marketPrice")))
                    if REPRINT_SETS.search(gname):
                        o = re.search(r"(\d+[a-z]?)/(\d+)", number_full or "")
                        orig = self._pkm_original(pcat, cid, o.group(1), int(o.group(2))) if (o and cid in pcat._cards) else None
                        if not orig:
                            self._need("tcgplayer", "pkm", prod["productId"], "reprint", f"{prod['name']} {number_full}: original not found")
                        for r in rows.values():
                            if r[0].split(":")[2] == cid and orig:
                                r[11], r[12] = 1, orig
                                self._bump("pkm_reprints_linked")
        ov = _overrides()
        for r in rows.values():
            if r[0] in ov:
                r[11], r[12] = 1, ov[r[0]]
        with self.con:
            self.con.execute("DELETE FROM extra_card")
            self.con.executemany("INSERT OR REPLACE INTO extra_card VALUES (?,?,?,?,?,?,?,?,?,?)", list(extra.values()))
            self.con.execute("DELETE FROM extra_set")
            self.con.executemany("INSERT OR REPLACE INTO extra_set VALUES (?,?,?)",
                                 sorted({(e[1], e[2], e[3]) for e in extra.values() if e[1].startswith("tcg-g")}))
        self._write(rows.values(), "pkm")

    def _pkm_original(self, pcat, cid: str, number: str, total: int) -> str | None:
        """The original card a Classic Collection reprint copies: same name, number and set size, released earlier."""
        c = pcat._cards[cid]
        name, rel = _cname(c[1]), c[9] or ""
        hits = [o for o, oc in pcat._cards.items() if o != cid and _cname(oc[1]) == name and _num(oc[4]) == _num(number)
                and (pcat.set(oc[3]) or {}).get("printed_total") == total and (oc[9] or "") < rel]
        return hits[0] if len(hits) == 1 else None

    # -- Yu-Gi-Oh! --------------------------------------------------------------------------------------------------
    def yugioh(self, ycat, tcg=None) -> None:
        self._reset("ygo", tcg)
        from ..identity import edition_variants
        rows: dict[str, list] = {}
        by_art: dict[str, list] = {}
        dated = _ygo_set_dates()
        # The set list dates English sets only; a Japanese set of the same name came out on another day.
        day = lambda p: p.release_date or (dated.get(norm(p.product_name or "")) if p.region.value == "EN_TCG" else None) or None
        for p in ycat.printings:
            for v in edition_variants(p, ycat):
                rows[v.id] = [v.id, "ygo", v.product_id, v.product_name, day(v) or day(p), v.set_code, None, v.rarity, v.edition,
                              {"JP_OCG": "ja"}.get(v.region.value, "en"), None, 0, None, ["identity"]]
            by_art.setdefault((p.artwork_id, p.region.value), []).append(p)
            fam = ycat.families.get(p.family_id)
            for v in edition_variants(p, ycat):
                rows[v.id][6] = (fam.names.get("en") or fam.names.get("ja")) if fam else None
        # A later printing of the same artwork (same region) is a reprint of the earliest one.
        rows_of: dict[str, list] = {}
        for r in rows.values():
            rows_of.setdefault(r[0].split("|")[0], []).append(r)
        for group in by_art.values():
            group = sorted((p for p in group if day(p)), key=lambda p: (day(p), p.id))
            if len(group) < 2:
                continue
            first = group[0]
            for p in group[1:]:
                if day(first) and day(p) and day(p) > day(first):      # both dated, or no link: never guessed
                    for r in rows_of.get(p.id, []):
                        r[11], r[12] = 1, first.id
        if tcg is not None:
            self._ygo_tcgplayer(ycat, tcg, rows)
        self._write(rows.values(), "ygo")

    def _ygo_tcgplayer(self, ycat, tcg, rows) -> None:
        """A Yu-Gi-Oh! printed code (LOB-EN001) names one card in one set, so each TCGplayer card is matched by its code
        and rarity (TCGplayer's set groups bundle or rename sets, so they only break ties). Then, per version of ours:
          one TCGplayer product              -> that version
          a reprint group ("2020 Date Reprint", "25th Anniversary Edition") -> its own version, a reprint of ours
          several products, told apart by a name tag ("Red", "8th Art", "Unlimited Misprint") -> one version each
          several products nothing tells apart -> needs a look
        A card TCGplayer sells that our list lacks is added, marked as coming from TCGplayer only."""
        from .tcgcsv import load
        groups, products, prices = load(tcg)
        by_code: dict[str, list] = {}
        for p in ycat.printings:
            if p.region.value == "EN_TCG" and p.set_code:
                by_code.setdefault(p.set_code.upper(), []).append(p)
        rows_of: dict[str, list] = {}
        for k in rows:
            rows_of.setdefault(k.split("|")[0], []).append(k)
        price_of, finishes, matched = {}, {}, []
        for g in groups:
            gid, gname = g["groupId"], g["name"]
            code, rel = (g.get("abbreviation") or "").upper(), (g.get("publishedOn") or "")[:10]
            for x in prices.get(gid, []):
                price_of[(x["productId"], x["subTypeName"])] = x
                finishes.setdefault(x["productId"], []).append(x["subTypeName"])
            reprint_tag = _reprint_group(gname)
            ours: dict[str, int] = {}
            for prod in products.get(gid, []):
                ext = {e["name"]: e["value"] for e in prod.get("extendedData") or []}
                num, rar = (ext.get("Number") or "").strip().upper(), (ext.get("Rarity") or "").strip()
                if not num and not rar:
                    continue                                   # boxes, packs, tins: not cards
                hit = [p for p in by_code.get(num, []) if norm(p.rarity) in _ygo_rarity(rar)] if num and num != "N/A" else []
                anniv = "25th anniversary" in norm(gname)       # a 25th Anniversary reprint and its original never cross
                hit = [p for p in hit if ("25th anniversary" in norm(p.product_name)) == anniv]
                if len({p.product_name for p in hit}) > 1:      # same code in two of our sets (an original and its reprint)
                    hit = _same_set(hit, _reprint_group(gname, strip=True))
                    if not hit:
                        self._need("tcgplayer", "ygo", prod["productId"], "card",
                                   f"{prod['name']} {num} {rar} in {gname}: in several of our sets; TCGplayer's set name does not say which")
                        self._bump("ygo_cards_ambiguous")
                        continue
                if len({p.id.rsplit("@", 1)[-1] for p in hit}) > 1:     # same code + rarity, different artwork
                    named = [p for p in hit if norm(_split_name(prod["name"])[0]) == norm(_family_name(ycat, p))]
                    hit = named if len({p.id.rsplit("@", 1)[-1] for p in named}) == 1 else hit
                if len({p.id.rsplit("@", 1)[-1] for p in hit}) > 1:
                    self._need("tcgplayer", "ygo", prod["productId"], "card",
                               f"{prod['name']} {num} {rar} in {gname}: several of our printings fit (different artwork)")
                    self._bump("ygo_cards_ambiguous")
                    continue
                if not hit:
                    pid = f"tcg-{prod['productId']}"
                    nm, sub = _split_name(prod["name"])
                    rows[pid] = [pid, "ygo", f"tcg-g{gid}", gname, rel or None, None if num in ("", "N/A") else num, nm, rar or None,
                                 None, "en", sub or None, 0, None, ["tcgplayer"]]
                    self._bump("ygo_cards_added")
                    for fin in finishes.get(prod["productId"], []) or [""]:
                        self._ygo_link(pid, prod["productId"], fin, price_of, tcg, rows)
                    continue
                base = sorted(hit, key=lambda p: p.id)[0]
                ours[base.product_name] = ours.get(base.product_name, 0) + 1
                self._bump("ygo_cards_matched")
                eds = [k for k in rows_of.get(base.id, ()) if k.split("|v:")[0] == k]
                for fin in finishes.get(prod["productId"], []) or [""]:
                    ed = YGO_EDITION.get(fin.lower())
                    pid = (f"{base.id}|ed:{ed}" if ed and f"{base.id}|ed:{ed}" in rows else base.id if base.id in rows
                           else eds[0] if len(eds) == 1 else None)
                    if pid is None:
                        self._need("tcgplayer", "ygo", prod["productId"], "card",
                                   f"{prod['name']} {num} {rar} in {gname}: TCGplayer does not say which edition ({fin or 'none given'})")
                        self._bump("ygo_cards_ambiguous")
                        continue
                    matched.append((pid, fin, prod, num, rar, gname, reprint_tag))
            our = max(ours, key=ours.get) if ours else f"tcg-g{gid}"
            self._source_set("tcgplayer", "ygo", gid, gname, code, rel, our)
            self._bump("ygo_sets_matched" if ours else "ygo_sets_added")
        # Resolve: per version of ours, which TCGplayer product is it.
        per: dict[tuple[str, str], list] = {}
        for m in matched:
            per.setdefault((m[0], m[1]), []).append(m)
        for (pid, fin), ms in per.items():
            plain = [m for m in ms if not m[6]]
            for m in ms:
                _, _, prod, num, rar, gname, rtag = m
                target = pid
                if rtag:                                       # the reprint wave is never the original
                    target = self._ygo_sub(rows, pid, rtag, reprint=True)
                if sum(1 for x in ms if x[6] == rtag) > 1:     # several products for one version: told apart by name
                    tag = _ygo_subversion(prod["name"], num, rar)
                    same = [x for x in ms if x[6] == rtag and _ygo_subversion(x[2]["name"], x[3], x[4]) == tag]
                    if len(same) > 1:
                        self._need("tcgplayer", "ygo", prod["productId"], "card",
                                   f"{prod['name']} {num} {rar} in {gname}: TCGplayer sells several products nothing tells apart")
                        self._bump("ygo_cards_ambiguous")
                        continue
                    if tag:
                        target = self._ygo_sub(rows, target, tag)
                    elif not plain and not rtag:
                        pass
                self._ygo_link(target, prod["productId"], fin, price_of, tcg, rows)

    def _ygo_sub(self, rows, pid: str, tag: str, reprint: bool = False) -> str:
        sub = f"{pid}|v:{tag}"
        if sub not in rows:
            rows[sub] = rows[pid][:13] + [["tcgplayer"]]
            rows[sub][0], rows[sub][10] = sub, " · ".join(x for x in (rows[pid][10], tag) if x)
            if reprint:
                rows[sub][11], rows[sub][12] = 1, pid.split("|")[0]
                self._bump("ygo_reprint_versions_added")
            else:
                self._bump("ygo_versions_added")
        return sub

    def _ygo_link(self, pid, product_id, fin, price_of, tcg, rows) -> None:
        rows[pid][13].append("tcgplayer")
        self.con.execute("INSERT OR REPLACE INTO xref VALUES (?,?,?,?)", (pid, "tcgplayer", str(product_id), fin))
        pr = price_of.get((product_id, fin))
        if pr and pr.get("marketPrice") is not None:
            self.con.execute("INSERT OR REPLACE INTO market_price VALUES (?,?,?,?,?,?)",
                             (pid, "tcgplayer", tcg.parent.name, pr.get("lowPrice"), pr.get("midPrice"), pr.get("marketPrice")))

    # -- writing ----------------------------------------------------------------------------------------------------
    def _write(self, rows, game: str) -> None:
        rows = list(rows)
        with self.con:
            self.con.execute("DELETE FROM printing WHERE game=?", (game,))
            self.con.executemany("INSERT OR REPLACE INTO printing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 [r[:13] + [",".join(sorted(set(r[13])))] for r in rows])
            self.con.execute("DELETE FROM needs_look WHERE game=?", (game,))
            self.con.executemany("INSERT OR REPLACE INTO needs_look VALUES (?,?,?,?,?,?)", [x for x in self.look if x[1] == game])
            self.con.execute("INSERT OR REPLACE INTO catalog_meta VALUES (?, ?)", (f"built_{game}", self.now))
            self.con.execute("INSERT OR REPLACE INTO catalog_meta VALUES (?, ?)", (f"stats_{game}",
                                                                                   json.dumps({k: v for k, v in self.stats.items() if k.startswith(game)})))
        self._bump(f"{game}_printings", len(rows))


SETLISTS = ROOT / "data" / "cache" / "setlists"
SETLIST_URLS = {"tcgdex_en_sets.json": "https://api.tcgdex.net/v2/en/sets",
                "ygoprodeck_cardsets.json": "https://db.ygoprodeck.com/api/v7/cardsets.php"}
# Out of our scope on purpose: Pokémon TCG Pocket (a phone game, no physical cards) and Yu-Gi-Oh! prints in languages
# other than English and Japanese.
OUT_OF_SCOPE = {"tcgdex": re.compile(r"^(?:A\d+[a-z]?|B\d+[a-z]?|P-A)$"),
                "ygoprodeck": re.compile(r"\((?:POR|SPA|ITA|FRE|GER|FR|DE|IT|PT|SP)\)\s*$", re.I)}


def fetch_setlists(root: Path = SETLISTS, get=None) -> None:
    """The two free set lists the completeness check compares against: one request each, once a day."""
    from .tcgcsv import _get
    get = get or _get
    root.mkdir(parents=True, exist_ok=True)
    for name, url in SETLIST_URLS.items():
        (root / name).write_text(json.dumps(get(url)))


def setlist_gaps(con, root: Path = SETLISTS) -> list[tuple]:
    """Sets the free set lists name that our master list lacks: (source, game, ext_id, detail). A set is present when
    its id, name or a hand-checked alias matches one of ours, or (Yu-Gi-Oh!) when we hold at least as many printed
    codes with its prefix as it has cards."""
    import html
    gaps = []
    aliases = {}
    if ALIASES.exists():
        with open(ALIASES, newline="") as f:
            for r in csv.DictReader(l for l in f if not l.startswith("#")):
                if r["source"] in ("tcgdex", "ygoprodeck"):
                    aliases[(r["source"], r["source_set"])] = r["our_set"]
    td = root / "tcgdex_en_sets.json"
    if td.exists():
        ids, names, size = set(), set(), {}
        for sid, sname, n in con.execute("SELECT set_id, set_name, COUNT(DISTINCT number) FROM printing WHERE game='pkm' GROUP BY set_id"):
            ids.add(sid); names.add(norm(sname)); size[sid] = n
        for s in json.loads(td.read_text()):
            if OUT_OF_SCOPE["tcgdex"].match(s["id"]) or not (s.get("cardCount") or {}).get("total"):
                continue                                       # out of scope, or a set with no cards listed
            a = aliases.get(("tcgdex", s["id"]))
            if a == "-" or s["id"] in ids or norm(s["name"]) in names or (a and all(x in ids for x in a.split("|"))):
                continue
            gaps.append(("tcgdex", "pkm", s["id"], f"{s['name']} ({s['cardCount'].get('total')} cards): not in our list"))
    yd = root / "ygoprodeck_cardsets.json"
    if yd.exists():
        names = {norm(html.unescape(n)) for (n,) in con.execute("SELECT DISTINCT set_name FROM printing WHERE game='ygo'")}
        codes: dict[str, set] = {}
        for (num,) in con.execute("SELECT number FROM printing WHERE game='ygo' AND number LIKE '%-%'"):
            codes.setdefault(num.split("-")[0].upper(), set()).add(num.upper())
        for s in json.loads(yd.read_text()):
            nm = html.unescape(s["set_name"])
            if OUT_OF_SCOPE["ygoprodeck"].search(nm) or aliases.get(("ygoprodeck", nm)) == "-":
                continue
            if norm(nm) in names or norm(re.sub(r"\s*\((?:set|series)\)$", "", nm)) in names:
                continue
            if len(codes.get((s.get("set_code") or "").upper(), ())) >= (s.get("num_of_cards") or 1):
                continue
            gaps.append(("ygoprodeck", "ygo", s.get("set_code") or nm, f"{nm} ({s.get('set_code')}, {s.get('num_of_cards')} cards): not in our list"))
    return gaps


def missing(con) -> dict[str, dict[str, int]]:
    """What a source lists that our list lacks, per game: sets and cards. Zero is the only acceptable number."""
    out: dict[str, dict[str, int]] = {}
    for game, kind, n in con.execute("SELECT game, kind, COUNT(*) FROM needs_look WHERE detail LIKE '%not in our list%' "
                                     "OR detail LIKE '%original not found%' GROUP BY game, kind"):
        out.setdefault(game, {})[kind] = n
    return out


def needs_look(con) -> dict[str, int]:
    """Links a source offers that no exact rule settles (two products for one version, no edition given...): per game.
    They are left unlinked, never guessed."""
    return dict(con.execute("SELECT game, COUNT(*) FROM needs_look WHERE NOT (detail LIKE '%not in our list%' "
                            "OR detail LIKE '%original not found%') GROUP BY game").fetchall())


def build(db: Path | str = DB, *, ygo=None, pkm=None, tcg_ygo=None, tcg_pkm=None, log=print, setlists: Path = SETLISTS) -> dict:
    b = Builder(db)
    if pkm is not None:
        b.pokemon(pkm, tcg_pkm)
    if ygo is not None:
        b.yugioh(ygo, tcg_ygo)
    gaps = setlist_gaps(b.con, setlists)
    with b.con:
        b.con.execute("DELETE FROM needs_look WHERE source IN ('tcgdex', 'ygoprodeck')")
        b.con.executemany("INSERT OR REPLACE INTO needs_look VALUES (?,?,?,?,?,?)",
                          [(src, game, ext, "set", detail, b.now) for src, game, ext, detail in gaps])
    b.stats["setlist_gaps"] = len(gaps)
    log(json.dumps(b.stats))
    return b.stats


def main() -> None:
    from ..identity_cache import IdentityCache
    from ..pkm_catalog import PkmCatalog
    from . import tcgcsv
    build(ygo=IdentityCache(str(ROOT / "data" / "cache" / "identity.sqlite")).catalog(), pkm=PkmCatalog(registry=False),
          tcg_ygo=tcgcsv.latest("ygo"), tcg_pkm=tcgcsv.latest("pkm"))


if __name__ == "__main__":
    main()
