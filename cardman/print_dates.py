"""Release dates per print code from YGOResources, cached per card as that API asks (X-Cache-Revision + /manifest).

Used to tell an original printing from its reprints. Only what the source states: YGOResources lists English
prints broadly but few Japanese ones, so a missing date means unknown, never "not the original".
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://db.ygoresources.com"
UA = "cardman/0.2 (private research tool)"


def _default_get(url: str) -> tuple[dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return dict(r.headers), r.read()


def code_key(code: str) -> str:
    """LOB-001 and LOB-EN001 are one print: early English codes had no language segment."""
    head, _, tail = code.upper().partition("-")
    digits = "".join(ch for ch in tail if ch.isdigit())
    return f"{head}-{digits}"


class PrintDates:
    def __init__(self, db_path: Path | str, get=_default_get):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.get = get
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS yr_card (konami_id INTEGER PRIMARY KEY, revision INTEGER, fetched_at TEXT, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS yr_meta (key TEXT PRIMARY KEY, value TEXT);""")

    def _fetch(self, kid: int) -> dict:
        headers, body = self.get(f"{BASE}/data/card/{kid}")
        rev = headers.get("X-Cache-Revision") or headers.get("x-cache-revision")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO yr_card VALUES (?,?,?,?)",
                            (kid, int(rev) if rev else None, datetime.now(timezone.utc).isoformat(), body.decode()))
            if rev:
                self.db.execute("INSERT OR REPLACE INTO yr_meta VALUES ('revision',?)", (str(rev),))
        return json.loads(body)

    def card(self, kid: int) -> dict:
        row = self.db.execute("SELECT body FROM yr_card WHERE konami_id=?", (kid,)).fetchone()
        return json.loads(row[0]) if row else self._fetch(kid)

    def refresh_changed(self) -> int:
        """Drop cached cards the manifest says changed since our revision."""
        row = self.db.execute("SELECT value FROM yr_meta WHERE key='revision'").fetchone()
        if not row:
            return 0
        _, body = self.get(f"{BASE}/manifest/{row[0]}")
        changed = (json.loads(body).get("data") or {}).get("card") or {}
        with self.db:
            n = sum(self.db.execute("DELETE FROM yr_card WHERE konami_id=?", (int(k),)).rowcount for k in changed if str(k).isdigit())
        return n

    def dates(self, kid: int, lang: str = "en") -> dict[str, str]:
        prints = ((self.card(kid).get("cardData") or {}).get(lang) or {}).get("prints") or []
        out: dict[str, str] = {}
        for p in prints:
            if p.get("code") and p.get("date"):
                k = code_key(p["code"])
                out[k] = min(out.get(k, p["date"]), p["date"])
        return out

    def describe(self, kid: int, code: str | None, lang: str = "en") -> dict:
        """{date, first_date, first_code, is_original}; is_original None when the source does not say."""
        d = self.dates(kid, lang)
        if not d:
            return {"date": None, "first_date": None, "first_code": None, "is_original": None}
        first_code, first_date = min(d.items(), key=lambda kv: kv[1])
        date = d.get(code_key(code)) if code else None
        return {"date": date, "first_date": first_date, "first_code": first_code,
                "is_original": (date == first_date) if date else None}
