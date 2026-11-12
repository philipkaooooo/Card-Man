"""Which live listings are (or might be) a given card, across platforms, from the engine's own identity weights.

A listing's possible printings carry probabilities. Summed per passcode they give the chance the listing is that card at
all; the best printing within the card gives the likely printing. Kept in a small index, rebuilt only when the engine's
results change, so a card page or a search result needs one indexed lookup.
  card_prob >= 0.95  -> "This card"            (the printing may still be open)
  card_prob <  0.95  -> "Might be this card"   (never mixed with the above)
Lots are counted, not listed: a lot is not a price for one card.
"""
from __future__ import annotations

import json
import re

THIS_CARD = 0.95
_PC = re.compile(r"@(\d+)")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_listing (passcode TEXT NOT NULL, key TEXT NOT NULL, card_prob REAL NOT NULL, printing_id TEXT,
  printing_prob REAL, label TEXT, all_in REAL, status TEXT, lot INTEGER NOT NULL, PRIMARY KEY (passcode, key));
CREATE INDEX IF NOT EXISTS card_listing_pc ON card_listing(passcode, card_prob);
CREATE TABLE IF NOT EXISTS card_listing_meta (k TEXT PRIMARY KEY, v TEXT);
"""
SITE = {"ebay": "eBay", "yahoo": "Yahoo! Auctions", "mercari": "Mercari", "130point": "130point", "manual": "You", "other": "Other"}


def site_of(source: str) -> str:
    s = (source or "").lower()
    return ("ebay" if s.startswith("ebay") else "yahoo" if s.startswith("yahoo") else "mercari" if s.startswith("mercari")
            else "130point" if s.startswith("130point") else "manual" if s == "manual" else "other")


def ensure_index(st) -> None:
    st.db.executescript(_SCHEMA)
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "opportunity" not in have:
        return
    marker = "|".join(str(x) for x in st.db.execute("SELECT COUNT(*), MAX(updated_at) FROM opportunity").fetchone())
    row = st.db.execute("SELECT v FROM card_listing_meta WHERE k='marker'").fetchone()
    if row and row[0] == marker:
        return
    rows = []
    for key, status, pj in st.db.execute("SELECT key, status, payload_json FROM opportunity"):
        d = json.loads(pj)
        sc = d.get("scenarios") or []
        total = sum(s.get("weight") or 0 for s in sc)
        if not total:
            continue
        per: dict[str, list] = {}
        for s in sc:
            m = _PC.search(s.get("printing_id") or "")
            if m:
                per.setdefault(m.group(1), []).append(s)
        lot = int("LOT" in (d.get("kinds") or []))
        for pc, ss in per.items():
            w = sum(s.get("weight") or 0 for s in ss)
            best = max(ss, key=lambda s: s.get("weight") or 0)
            rows.append((pc, key, w / total, best.get("printing_id"), (best.get("weight") or 0) / w if w else None, best.get("label"),
                         d.get("all_in_home"), status, lot))
    with st.db:
        st.db.execute("DELETE FROM card_listing")
        st.db.executemany("INSERT OR REPLACE INTO card_listing VALUES (?,?,?,?,?,?,?,?,?)", rows)
        st.db.execute("INSERT OR REPLACE INTO card_listing_meta VALUES ('marker', ?)", (marker,))
    try:
        from datetime import datetime, timezone
        from .engine import load_profile
        from .history import update_ask_daily
        try:
            home = load_profile()["home_currency"]
        except Exception:
            home = "TWD"
        update_ask_daily(st, datetime.now(timezone.utc), home)
    except Exception:
        pass   # the ask series is a convenience; never block the listings index


def listings_for(st, passcode: str) -> list[dict]:
    """Live, not rejected listings that are or might be this card, with their listing row and engine payload."""
    ensure_index(st)
    out = []
    q = ("SELECT c.key, c.card_prob, c.printing_id, c.printing_prob, c.label, c.all_in, c.status, c.lot, l.source, l.title, l.url, l.kind, "
         "l.price, l.currency, l.data_json, o.payload_json FROM card_listing c JOIN listing l ON l.key=c.key JOIN opportunity o ON o.key=c.key "
         "WHERE c.passcode=? AND l.ended_at IS NULL AND c.status<>'REJECTED' ORDER BY c.all_in IS NULL, c.all_in")
    for (key, cp, pid, pp, label, all_in, status, lot, source, title, url, kind, price, cur, dj, pj) in st.db.execute(q, (passcode,)):
        data = json.loads(dj or "{}")
        out.append({"key": key, "card_prob": cp, "printing_id": pid, "printing_prob": pp, "label": label, "all_in": all_in, "status": status,
                    "lot": bool(lot), "site": site_of(source), "title": title, "url": url, "kind": kind, "price": price, "currency": cur,
                    "shipping": data.get("shipping"), "grade_company": data.get("grade_company"), "grade": data.get("grade"),
                    "condition": data.get("condition"), "payload": json.loads(pj)})
    return out


def summary(st, passcodes: list[str]) -> dict[str, tuple[int, float | None]]:
    """For search results: how many live single listings are this card, and the lowest all-in (home currency)."""
    if not passcodes:
        return {}
    ensure_index(st)
    ph = ",".join("?" * len(passcodes))
    rows = st.db.execute(
        f"SELECT c.passcode, COUNT(*), MIN(c.all_in) FROM card_listing c JOIN listing l ON l.key=c.key WHERE c.passcode IN ({ph}) "
        f"AND c.card_prob>=? AND c.lot=0 AND c.status<>'REJECTED' AND l.ended_at IS NULL GROUP BY c.passcode", (*passcodes, THIS_CARD)).fetchall()
    return {pc: (n, mn) for pc, n, mn in rows}


def coverage(st) -> dict[str, dict]:
    """What we actually hold per platform: live listings and recorded sales. Never more than the data shows."""
    out: dict[str, dict] = {}
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for src, n in (st.db.execute("SELECT source, COUNT(*) FROM listing WHERE ended_at IS NULL AND kind IN ('ask','bid','auction') GROUP BY source") if "listing" in have else []):
        out.setdefault(site_of(src), {"live": 0, "sold": 0})["live"] += n
    for src, n in st.db.execute("SELECT source, COUNT(*) FROM observation WHERE synthetic=0 AND evidence_type IN "
                                "('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') GROUP BY source"):
        out.setdefault(site_of(src), {"live": 0, "sold": 0})["sold"] += n
    return out


def coverage_line(cov: dict[str, dict]) -> str:
    order = [s for s in SITE if s in cov]
    live = [SITE[s] for s in order if cov[s]["live"]]
    sold_only = [SITE[s] for s in order if cov[s]["sold"] and not cov[s]["live"] and s != "manual"]
    parts = []
    if live:
        parts.append(" · ".join(live) + " live")
    if sold_only:
        parts.append(" · ".join(sold_only) + " sold only")
    missing = [SITE[s] for s in ("yahoo", "mercari") if s not in cov]
    if missing:
        parts.append(" · ".join(missing) + ": nothing yet (add their sold pages with the bookmark)")
    return " · ".join(parts) or "No listings collected yet"
