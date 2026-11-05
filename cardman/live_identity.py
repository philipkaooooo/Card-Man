"""Live identity cross-checks against YGOResources and YGOPRODeck (CLAUDE.md sections 1-2).

Tested live on 2026-11-05:
* YGOResources /data/card/{konami_id}: per-locale print codes with release dates, including
  Asian-English ("ae"). No rarity, no edition. Every response carries X-Cache-Revision;
  /manifest/{revision} lists changed paths (leaves are 1), used here for invalidation.
  /data/meta/index/printcode (documented) answered 404.
* YGOPRODeck /api/v7/cardinfo.php?id={passcode}: EN TCG card_sets with set_rarity. No edition,
  no release date. card_prices is ignored: it is not per-print sold evidence.

Both are fetched per card on demand (no full-database crawl), at most one request per second,
and raw bodies go to the raw evidence store before parsing. Results never overwrite the
catalog's source facts; they are stored as cross-check records next to them.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .evidence import RawEvidenceStore
from .identity import parse_set_code
from .identity_sources import slug
from .models import Printing, Region

CROSSCHECK_VERSION = "crosscheck-0.1.0"
USER_AGENT = "cardman/0.3 (private research tool; low volume, cached)"
YGOR = "https://db.ygoresources.com"
YPD = "https://db.ygoprodeck.com/api/v7"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_doc (
    source TEXT NOT NULL, key TEXT NOT NULL, revision TEXT, fetched_at TEXT NOT NULL, raw_ref TEXT NOT NULL,
    body TEXT NOT NULL, PRIMARY KEY (source, key));
CREATE TABLE IF NOT EXISTS crosscheck (
    printing_id TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL, source_revision TEXT,
    verdict TEXT NOT NULL, detail_json TEXT NOT NULL, PRIMARY KEY (printing_id, source));
CREATE TABLE IF NOT EXISTS remote_printing (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, family_id TEXT NOT NULL, data_json TEXT NOT NULL,
    first_seen TEXT NOT NULL, source_revision TEXT);
"""


class Unavailable(RuntimeError):
    pass


class Http:
    def __init__(self, min_interval_s: float = 1.0, opener=urllib.request.urlopen, sleep=time.sleep):
        self.min_interval_s, self._open, self._sleep = min_interval_s, opener, sleep
        self._last = 0.0

    def get(self, url: str) -> tuple[bytes, dict]:
        delay = 2.0
        for attempt in range(4):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with self._open(req, timeout=30) as r:
                    return r.read(), {k.lower(): v for k, v in r.headers.items()}
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise Unavailable(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, OSError) as e:
                if attempt < 3:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise Unavailable(f"{url}: {e}") from e
        raise Unavailable(url)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrossCheck:
    printing_id: str
    source: str
    verdict: str          # CONFIRMED | NOT_LISTED | NOT_COVERED | MISMATCH | NOT_APPLICABLE
    detail: dict = field(default_factory=dict)


class LiveIdentity:
    """Fetch, cache and cross-check. `db` is the identity cache's sqlite connection."""

    def __init__(self, db: sqlite3.Connection, raw: RawEvidenceStore, http: Http | None = None):
        self.db, self.raw, self.http = db, raw, http or Http()
        self.db.executescript(_SCHEMA)

    # -- cached fetches --------------------------------------------------------------------------
    def _cached(self, source: str, key: str):
        return self.db.execute("SELECT revision, body FROM remote_doc WHERE source=? AND key=?",
                               (source, key)).fetchone()

    def _store(self, source: str, key: str, url: str, body: bytes, revision: str | None) -> dict:
        ref = self.raw.put(source, body, {"url": url, "captured_at": _now(), "revision": revision,
                                          "adapter": CROSSCHECK_VERSION})
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO remote_doc VALUES (?,?,?,?,?,?)",
                            (source, key, revision, _now(), ref, body.decode("utf-8")))
        return json.loads(body)

    def konami_releases(self, konami_id: int) -> list:
        from .konami_db import CARD_URL, parse_card_page
        key = f"card:{konami_id}:ja"
        hit = self._cached("konami_db", key)
        if hit:
            return parse_card_page(hit[1])
        url = CARD_URL.format(cid=konami_id, loc="ja")
        body, _ = self.http.get(url)
        ref = self.raw.put("konami_db", body, {"url": url, "captured_at": _now(), "adapter": CROSSCHECK_VERSION})
        page = body.decode("utf-8")
        rel = parse_card_page(page)   # parse before caching, so a layout change is not cached
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO remote_doc VALUES (?,?,?,?,?,?)",
                            ("konami_db", key, None, _now(), ref, page))
        return rel

    def ygor_revision(self) -> str | None:
        row = self.db.execute("SELECT max(CAST(revision AS INTEGER)) FROM remote_doc WHERE source='ygoresources'"
                              ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def refresh_ygor(self) -> list[str]:
        """Drop cached YGOResources docs that changed since the newest revision we hold."""
        rev = self.ygor_revision()
        if rev is None:
            return []
        body, headers = self.http.get(f"{YGOR}/manifest/{rev}")
        changed = []

        def walk(x, p=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{p}/{k}")
            else:
                changed.append(p)
        walk(json.loads(body))
        stale = [p for p in changed if p.startswith("/data/card/")]
        with self.db:
            for p in stale:
                self.db.execute("DELETE FROM remote_doc WHERE source='ygoresources' AND key=?", (p,))
        return stale

    def ygor_card(self, konami_id: int) -> dict:
        key = f"/data/card/{konami_id}"
        hit = self._cached("ygoresources", key)
        if hit:
            return json.loads(hit[1])
        body, headers = self.http.get(YGOR + key)
        return self._store("ygoresources", key, YGOR + key, body, headers.get("x-cache-revision"))

    def ypd_card(self, passcode: str) -> dict | None:
        key = f"cardinfo:{passcode}"
        hit = self._cached("ygoprodeck", key)
        if hit:
            return json.loads(hit[1])
        url = f"{YPD}/cardinfo.php?id={passcode}"
        try:
            body, _ = self.http.get(url)
        except Unavailable as e:
            if "HTTP 400" in str(e):   # YGOPRODeck answers 400 for an unknown id
                return None
            raise
        ver = None
        try:
            vbody, _ = self.http.get(f"{YPD}/checkDBVer.php")
            ver = json.loads(vbody)[0].get("database_version")
        except Exception:
            pass
        return self._store("ygoprodeck", key, url, body, ver)

    # -- cross-checks ----------------------------------------------------------------------------
    def check_family(self, konami_id: int, passcodes: list[str], printings: list[Printing]) -> list[CrossCheck]:
        out: list[CrossCheck] = []
        yg = self.ygor_card(konami_id)
        rev = self.ygor_revision()
        cd = yg.get("cardData") or {}
        en_codes = {p["code"].upper(): p.get("date") for p in (cd.get("en") or {}).get("prints") or []}
        ja_codes = {p["code"].upper(): p.get("date") for p in (cd.get("ja") or {}).get("prints") or []}
        ae_codes = {p["code"].upper(): p.get("date") for p in (cd.get("ae") or {}).get("prints") or []}
        ypd_sets = []
        for pw in passcodes:
            card = self.ypd_card(pw)
            for c in (card or {}).get("data") or []:
                ypd_sets += [(s.get("set_code", "").upper(), s.get("set_rarity"), s.get("set_name")) for s in
                             c.get("card_sets") or []]
        konami = self.konami_releases(konami_id)
        k_codes: dict[str, list] = {}
        for k in konami:
            if k.code:
                k_codes.setdefault(k.code.upper(), []).append(k)
        for p in printings:
            if p.region == Region.JP_OCG:
                if p.set_code and p.set_code.upper() in k_codes:
                    ks = k_codes[p.set_code.upper()]
                    # Konami rows are per product (pid), so the date is attributable when one pid uses the code
                    one_pid = len({k.pid for k in ks}) == 1
                    out.append(CrossCheck(p.id, "konami_db", "CONFIRMED", {
                        "release_date": ks[0].date if one_pid else None,
                        "code_dates": sorted({k.date for k in ks if k.date}),
                        "konami_rarities": sorted({k.rarity_label or "" for k in ks}),
                        "konami_pids": sorted({k.pid or "" for k in ks}), "konami_product": ks[0].product}))
                elif p.set_code:
                    out.append(CrossCheck(p.id, "konami_db", "NOT_LISTED", {"konami_rows": len(konami)}))
                else:
                    out.append(CrossCheck(p.id, "konami_db", "NOT_APPLICABLE", {
                        "why": "no print code; Konami lists code-less releases by Japanese product name only",
                        "konami_codeless": [[k.date, k.product, k.rarity_label] for k in konami if not k.code]}))
            if not p.set_code:
                out.append(CrossCheck(p.id, "ygoresources", "NOT_APPLICABLE", {"why": "no printed set code"}))
                continue
            code = p.set_code.upper()
            table = en_codes if p.region == Region.EN_TCG else ja_codes
            if p.region == Region.JP_OCG and code not in table:
                # YGOResources lists few JP prints (BEWD: 1 of ~70 on 2026-11-05); Konami DB is the JP check
                out.append(CrossCheck(p.id, "ygoresources", "NOT_COVERED", {"listed_count": len(table)}))
                continue
            if code in table:
                # YGOResources dates the CODE (one date per code), not the product: LOB-EN001 carries
                # 2023-04-21 although YAML Yugi also lists it under the original LOB product. Attach the
                # date only when one product in this region uses the code.
                sharing = {q.product_name for q in printings if q.region == p.region and q.set_code
                           and q.set_code.upper() == code}
                d = {"code_date": table[code]}
                if len(sharing) == 1:
                    d["release_date"] = table[code]
                else:
                    d["date_scope"] = f"code shared by {len(sharing)} products; date not attributed to one"
                if code in ae_codes:
                    d["warning"] = f"{code} is also an Asian-English print code (released {ae_codes[code]})"
                out.append(CrossCheck(p.id, "ygoresources", "CONFIRMED", d))
            else:
                out.append(CrossCheck(p.id, "ygoresources", "NOT_LISTED",
                                      {"listed_count": len(table), "region": p.region.value}))
            if p.region == Region.EN_TCG:
                hits = [s for s in ypd_sets if s[0] == code]
                if not hits:
                    out.append(CrossCheck(p.id, "ygoprodeck", "NOT_LISTED", {}))
                elif any(s[1] == p.rarity and slug(s[2] or "") == slug(p.product_name or "") for s in hits):
                    out.append(CrossCheck(p.id, "ygoprodeck", "CONFIRMED", {}))
                elif any(s[1] == p.rarity for s in hits):
                    out.append(CrossCheck(p.id, "ygoprodeck", "MISMATCH",
                                          {"why": "rarity listed under a different product name",
                                           "ygoprodeck": [list(s) for s in hits]}))
                else:
                    out.append(CrossCheck(p.id, "ygoprodeck", "MISMATCH",
                                          {"why": "code listed with other rarities",
                                           "ygoprodeck": [list(s) for s in hits]}))
        with self.db:
            for c in out:
                srev = rev if c.source == "ygoresources" else None
                if c.source == "konami_db" and c.verdict == "NOT_APPLICABLE":
                    c.detail = {"why": c.detail["why"], "konami_codeless_count": len(c.detail["konami_codeless"])}
                self.db.execute("INSERT OR REPLACE INTO crosscheck VALUES (?,?,?,?,?,?)",
                                (c.printing_id, c.source, _now(), srev, c.verdict, json.dumps(c.detail)))
        return out

    def asian_english_printings(self, konami_id: int, family_id: str, artwork_id: str) -> list[Printing]:
        """AE print codes from YGOResources, as quarantined printings (rarity not given by this source)."""
        yg = self.ygor_card(konami_id)
        rev = self.ygor_revision()
        out = []
        for pr in ((yg.get("cardData") or {}).get("ae") or {}).get("prints") or []:
            code = pr["code"].upper()
            p = Printing(
                id=f"p:ASIAN_EN_OCG_QUARANTINE:{code}:ygoresources:unknown@k{konami_id}", family_id=family_id,
                artwork_id=artwork_id, region=Region.ASIAN_EN_OCG_QUARANTINE, language="en",
                product_id=f"ASIAN_EN:{code.split('-')[0]}", product_name=None, rarity="UNKNOWN",
                set_code=code, release_date=pr.get("date") or None, identity_status="incomplete",
                variant_evidence=("Asian-English OCG print listed by YGOResources; rarity/product not given",),
                source_refs=(f"ygoresources@{rev}:/data/card/{konami_id}",))
            out.append(p)
            with self.db:
                from .identity_cache import _printing_json
                self.db.execute("INSERT OR IGNORE INTO remote_printing VALUES (?,?,?,?,?,?)",
                                (p.id, "ygoresources", family_id, _printing_json(p), _now(), rev))
        return out


# Konami's Japanese rarity labels ("…仕様") -> the English names the catalog uses. Unknown labels stay
# unmapped and the printing is marked incomplete rather than guessed.
KONAMI_RARITY = {"ノーマル": "Common", "レア": "Rare", "スーパーレア": "Super Rare", "ウルトラレア": "Ultra Rare",
                 "シークレットレア": "Secret Rare", "アルティメットレア": "Ultimate Rare",
                 "ノーマルパラレルレア": "Normal Parallel Rare", "コレクターズレア": "Collector's Rare",
                 "プリズマティックシークレットレア": "Prismatic Secret Rare",
                 "クォーターセンチュリーシークレットレア": "Quarter Century Secret Rare",
                 "ホログラフィックレア": "Holographic Rare", "エクストラシークレットレア": "Extra Secret Rare",
                 "ミレニアム": "Millennium Rare"}


def konami_only_printings(live: "LiveIdentity", konami_id: int, family_id: str, artwork_id: str,
                          known_codes: set[str]) -> list[Printing]:
    """JP releases Konami lists with a print code that the catalog lacks, added as Konami-sourced printings."""
    from .identity_cache import _printing_json
    out = []
    for k in live.konami_releases(konami_id):
        if not k.code or k.code.upper() in known_codes:
            continue
        seg = parse_set_code(k.code)
        if not seg or seg["lang"] not in ("", "JP"):
            continue   # e.g. 2020-EN001 listed on the JP page: not a JP OCG print
        label = (k.rarity_label or "").replace("仕様", "").strip()
        rar = KONAMI_RARITY.get(label)
        p = Printing(
            id=f"p:JP_OCG:{k.code.upper()}:konami-{k.pid}:{slug(rar or label or 'unknown')}@k{konami_id}",
            family_id=family_id, artwork_id=artwork_id, region=Region.JP_OCG, language="ja",
            product_id=f"JP_OCG:konami-pid-{k.pid}", product_name=k.product, rarity=rar or (label or "UNKNOWN"),
            set_code=k.code.upper(), release_date=k.date, identity_status="documented" if rar else "incomplete",
            variant_evidence=(f"listed by Konami DB (pid {k.pid}, label {k.rarity_label}) but not by YAML Yugi",)
            + (() if rar else (f"Konami rarity label '{k.rarity_label}' not mapped",)),
            source_refs=(f"konami_db:cid={konami_id}:pid={k.pid}",), verified_against_source=True)
        if any(o.id == p.id for o in out):
            continue   # Konami repeats a row per artwork image
        out.append(p)
        with live.db:
            live.db.execute("INSERT OR IGNORE INTO remote_printing VALUES (?,?,?,?,?,?)",
                            (p.id, "konami_db", family_id, _printing_json(p), _now(), None))
    return out


def summarize(checks: list[CrossCheck]) -> dict:
    s: dict = {}
    for c in checks:
        s.setdefault(c.source, {}).setdefault(c.verdict, 0)
        s[c.source][c.verdict] += 1
    return s


def main():
    import argparse
    from .identity_cache import IdentityCache
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="cross-check cached printings against YGOResources/YGOPRODeck")
    ap.add_argument("konami_ids", nargs="+", type=int)
    ap.add_argument("--db", default=str(root / "data" / "cache" / "identity.sqlite"))
    ap.add_argument("--raw", default=str(root / "data" / "raw"))
    a = ap.parse_args()
    cache = IdentityCache(a.db)
    live = LiveIdentity(cache.db, RawEvidenceStore(Path(a.raw)))
    stale = live.refresh_ygor()
    report = {"invalidated_by_manifest": stale, "cards": {}}
    for kid in a.konami_ids:
        fam = f"fam:k{kid}"
        pws = [r[0] for r in cache.db.execute("SELECT passcode FROM source_file WHERE family_id=? AND retired_rev"
                                              " IS NULL", (fam,))]
        prints = [p for p in cache.catalog().by_family.get(fam, []) if p.region != Region.ASIAN_EN_OCG_QUARANTINE]
        checks = live.check_family(kid, pws, prints)
        ae = live.asian_english_printings(kid, fam, f"art:pw{pws[0]}" if pws else f"art:k{kid}")
        konami = live.konami_releases(kid)
        ko = konami_only_printings(live, kid, fam, f"art:pw{pws[0]}" if pws else f"art:k{kid}",
                                   {p.set_code.upper() for p in prints if p.set_code and p.region == Region.JP_OCG})
        known = {p.set_code.upper() for p in prints if p.set_code and p.region == Region.JP_OCG}
        report["cards"][kid] = {"konami_codes_missing_from_catalog": sorted({k.code for k in konami if k.code}
                                                                             - known),
                                "konami_codeless": [[k.date, k.product, k.rarity_label] for k in konami if not k.code],
                                "konami_only_printings_added": [p.id for p in ko],
                                "passcodes": pws, "printings": len(prints), "summary": summarize(checks),
                                "asian_english_codes": [p.set_code for p in ae],
                                "not_listed_or_mismatch": [(c.printing_id, c.source, c.verdict, c.detail)
                                                           for c in checks if c.verdict in ("NOT_LISTED", "MISMATCH")][:12]}
    report["ygoresources_revision"] = live.ygor_revision()
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
