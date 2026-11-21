"""Saved sold pages in, sale records out, and a review list for confirming which exact printing each sale was.

A sale only becomes a comparable for a printing when that printing is certain: either the evidence resolves it, or you
pick it on the review list (stored as an identity event; the sale record itself is never edited). Until then the sale is
kept against every printing it could be, which prices nothing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .engine import TickReport
from .market_sources import sold_rows_to_listings
from .sold_pages import read_page

_SCHEMA = """CREATE TABLE IF NOT EXISTS import_row (batch TEXT NOT NULL, file TEXT, site TEXT, key TEXT NOT NULL, obs_id TEXT NOT NULL,
             at TEXT NOT NULL, PRIMARY KEY (batch, obs_id))"""


def import_pages(engine, files: list[tuple[str, bytes]], now: datetime) -> dict:
    engine.db.execute(_SCHEMA)
    batch = hashlib.sha256(("|".join(f"{n}:{hashlib.sha256(d).hexdigest()}" for n, d in files) + now.isoformat()).encode()).hexdigest()[:12]
    rep = TickReport(at=now.isoformat())
    out = {"batch": batch, "pages": [], "rows": 0, "new": 0, "errors": []}
    for name, data in files:
        try:
            page = read_page(name, data, now)
        except Exception as x:                     # a broken file must not stop the others
            out["errors"].append(f"{name}: could not read ({type(x).__name__})")
            continue
        if not page.site:
            out["errors"].append(f"{name}: not a sold-results page from eBay, 130point, Yahoo! Auctions or Mercari")
            continue
        ref = engine.raw.put(f"sold_pages/{page.site}", page.stored_html.encode(),
                             {"file": name, "site": page.site, "sha256": page.sha256, "captured_at": now.isoformat(),
                              "synthetic": page.synthetic, "note": "seller and buyer elements removed before storing"})
        listings = sold_rows_to_listings(page, ref)
        before = engine.store.db.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        for r in listings:
            engine.remember(r, now, rep)
            engine._take_evidence(r, now, rep)
            oid = r.to_observation((), now).id
            with engine.db:
                engine.db.execute("INSERT OR IGNORE INTO import_row VALUES (?,?,?,?,?,?)", (batch, name, page.site, r.key, oid, now.isoformat()))
        after = engine.store.db.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        out["pages"].append({"file": name, "site": page.site, "rows": len(listings), "new": after - before, "synthetic": page.synthetic})
        out["rows"] += len(listings)
        out["new"] += after - before
    out["errors"] += rep.errors
    return out


def batch_rows(st, batch: str | None, limit: int = 300) -> list[dict]:
    st.db.execute(_SCHEMA)
    q = ("SELECT r.batch, r.file, r.site, r.key, r.obs_id, r.at FROM import_row r "
         + ("WHERE r.batch=? " if batch else "") + "ORDER BY r.at DESC, r.rowid LIMIT ?")
    rows = []
    for b, f, site, key, oid, at in st.db.execute(q, (batch, limit) if batch else (limit,)):
        try:
            o = st.effective(oid)
        except KeyError:
            continue
        confirmed = st.db.execute("SELECT printing_id FROM observation_identity WHERE obs_id=? ORDER BY rowid DESC LIMIT 1", (oid,)).fetchone()
        title = st.db.execute("SELECT title FROM listing WHERE key=?", (key,)).fetchone()
        rows.append({"batch": b, "file": f, "site": site, "key": key, "obs": o, "title": title[0] if title else key,
                     "confirmed": confirmed[0] if confirmed else None})
    return rows


def batches(st, limit: int = 12) -> list[tuple]:
    st.db.execute(_SCHEMA)
    return st.db.execute("SELECT batch, MIN(at), COUNT(*), GROUP_CONCAT(DISTINCT site) FROM import_row GROUP BY batch "
                         "ORDER BY MIN(at) DESC LIMIT ?", (limit,)).fetchall()
