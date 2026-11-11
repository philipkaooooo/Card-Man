"""Daily FX rates kept in the store, so old sales convert at a rate near their own date (README gap 4).

Sources: open.er-api.com for today; the fawazahmed0 daily snapshots on jsDelivr for past dates. Rates carry their
source and date. A missing rate stays missing: conversion then raises rather than using a stale number.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .fx import FxRate, FxTable

UA = "cardman/0.2 (private research tool)"
LATEST = "https://open.er-api.com/v6/latest/{base}"
HISTORY = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{base}.min.json"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        return json.loads(r.read())


class FxStore:
    """Fetches daily rates into the evidence store's own fx_rate table (one table for every rate the system uses)."""

    def __init__(self, path: Path | str, get_json=_get_json):
        from .store import Store
        self.store = Store(path)          # creates the schema, including fx_rate
        self.db = self.store.db
        self.get_json = get_json

    def put(self, base: str, quote: str, day: str, mid, as_of: datetime, source: str) -> None:
        from .store import ImmutableViolation
        try:
            self.store.add_fx(FxRate(base, quote, Decimal(str(mid)), as_of, source))
        except ImmutableViolation:
            pass   # same source and time already stored with another value: keep the first

    def _on_day(self, base: str, quote: str, day: str):
        return self.db.execute("SELECT as_of, mid, source FROM fx_rate WHERE base=? AND quote=? AND substr(as_of,1,10)<=?"
                               " ORDER BY as_of DESC LIMIT 1", (base, quote, day)).fetchone()

    def sync_latest(self, base: str, quotes: tuple[str, ...]) -> int:
        d = self.get_json(LATEST.format(base=base))
        if d.get("result") != "success":
            raise RuntimeError(f"FX source refused: {d.get('result')}")
        as_of = datetime.fromtimestamp(d["time_last_update_unix"], timezone.utc)
        for q in quotes:
            self.put(base, q, as_of.date().isoformat(), Decimal(str(d["rates"][q])), as_of, "open.er-api.com")
        return len(quotes)

    def backfill(self, base: str, quote: str, day: date) -> bool:
        iso = day.isoformat()
        if self.db.execute("SELECT 1 FROM fx_rate WHERE base=? AND quote=? AND substr(as_of,1,10)=?",
                           (base, quote, iso)).fetchone():
            return True
        try:
            d = self.get_json(HISTORY.format(day=iso, base=base.lower()))
            mid = d[base.lower()][quote.lower()]
        except Exception:
            return False
        self.put(base, quote, iso, Decimal(str(mid)), datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                 "fawazahmed0/currency-api")
        return True

    def rates_for(self, pairs: list[tuple[str, str]], moments: list[datetime]) -> list[FxRate]:
        """The rate on each moment's own date, backfilled when absent. Today falls back to the latest within a day."""
        out: dict[tuple, FxRate] = {}
        for base, quote in pairs:
            if base == quote:
                continue
            for m in moments:
                d = m.astimezone(timezone.utc).date()
                self.backfill(base, quote, d)
                row = self._on_day(base, quote, d.isoformat())
                if row:
                    out[(base, quote, row[0][:10])] = FxRate(base, quote, Decimal(row[1]), datetime.fromisoformat(row[0]), row[2])
        return list(out.values())

    def table(self, pairs: list[tuple[str, str]], moments: list[datetime]) -> FxTable:
        return FxTable(self.rates_for(pairs, moments), max_age=timedelta(days=2))

    def stats(self) -> dict:
        n, last = self.db.execute("SELECT COUNT(*), MAX(as_of) FROM fx_rate").fetchone()
        return {"rates": n, "latest": last}


def rate_rows(rates: list[FxRate]) -> list[dict]:
    """Listing-file form used by the slice and stored inside each snapshot."""
    return [{"base": r.base, "quote": r.quote, "mid": str(r.mid), "as_of": r.as_of.isoformat(), "source": r.source} for r in rates]
