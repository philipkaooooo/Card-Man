"""Private learning: what actually happened to your decisions. Append-only; corrections are new events.

Calibration comes only from your own completed outcomes. It can lower future resale estimates; it never raises them
above what the sales show, and a few results move it only a little.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

KINDS = ("NEGOTIATION", "REJECTED", "PURCHASE", "GRADING_RESULT", "SALE", "MISTAKE")
REQUIRED = {"PURCHASE": ("printing_id", "price_home", "all_in_home"), "SALE": ("purchase_id", "gross_home", "net_home"),
            "GRADING_RESULT": ("purchase_id", "company", "grade", "cost_home"),
            "REJECTED": ("reason",), "MISTAKE": ("tag", "what"), "NEGOTIATION": ("asked_home", "outcome")}
SHRINK = 5.0   # outcomes needed before your own data counts as much as the prior of "no adjustment"


@dataclass(frozen=True)
class Calibration:
    n_sales: int
    resale_bias: float            # realized / predicted resale, shrunk, never above 1
    raw_ratio: float | None
    cost_overrun: float           # actual all-in / predicted all-in
    median_months_to_sell: float | None
    win_rate: float | None
    realized_profit: float
    capital_days: float
    mistakes: dict[str, int]


class Ledger:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path))
        self.db.execute("""CREATE TABLE IF NOT EXISTS ledger_event (id TEXT PRIMARY KEY, kind TEXT NOT NULL, at TEXT NOT NULL,
                           listing_key TEXT, printing_id TEXT, data_json TEXT NOT NULL, recorded_at TEXT NOT NULL)""")

    def add(self, kind: str, data: dict, *, at: datetime, listing_key: str | None = None, now: datetime | None = None) -> str:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r}")
        missing = [f for f in REQUIRED[kind] if data.get(f) in (None, "")]
        if missing:
            raise ValueError(f"{kind} needs {', '.join(missing)}")
        if at.tzinfo is None:
            raise ValueError("ledger time must be timezone-aware")
        for ref in ("purchase_id",):
            if ref in REQUIRED[kind] and not self.get(data[ref]):
                raise ValueError(f"no purchase {data[ref]}")
        eid = f"{kind.lower()}-{uuid.uuid4().hex[:10]}"
        with self.db:
            self.db.execute("INSERT INTO ledger_event VALUES (?,?,?,?,?,?,?)",
                            (eid, kind, at.isoformat(), listing_key, data.get("printing_id"),
                             json.dumps(data, ensure_ascii=False, sort_keys=True), (now or at).isoformat()))
        return eid

    def get(self, eid: str) -> dict | None:
        r = self.db.execute("SELECT id, kind, at, listing_key, printing_id, data_json FROM ledger_event WHERE id=?", (eid,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "kind": r[1], "at": r[2], "listing_key": r[3], "printing_id": r[4], **json.loads(r[5])}

    def events(self, kind: str | None = None, as_of: datetime | None = None, limit: int = 1000) -> list[dict]:
        q = "SELECT id, kind, at, listing_key, printing_id, data_json FROM ledger_event"
        cond, args = [], []
        if kind:
            cond.append("kind=?"); args.append(kind)
        if as_of:
            cond.append("at<=?"); args.append(as_of.isoformat())
        q += (" WHERE " + " AND ".join(cond) if cond else "") + " ORDER BY at DESC LIMIT ?"
        return [self._row(r) for r in self.db.execute(q, (*args, limit))]

    def calibration(self, as_of: datetime | None = None, *, route: str | None = None) -> Calibration:
        buys = {e["id"]: e for e in self.events("PURCHASE", as_of)}
        if route:
            buys = {k: v for k, v in buys.items() if v.get("route") == route}
        grades: dict[str, float] = {}
        for g in self.events("GRADING_RESULT", as_of):
            grades[g["purchase_id"]] = grades.get(g["purchase_id"], 0.0) + float(g["cost_home"])
        ratios, overruns, months, profits, days = [], [], [], [], 0.0
        for s in self.events("SALE", as_of):
            b = buys.get(s["purchase_id"])
            if not b:
                continue
            if b.get("predicted_resale_home"):
                ratios.append(float(s["gross_home"]) / float(b["predicted_resale_home"]))
            if b.get("predicted_all_in_home"):
                overruns.append(float(b["all_in_home"]) / float(b["predicted_all_in_home"]))
            held = (datetime.fromisoformat(s["at"]) - datetime.fromisoformat(b["at"])).total_seconds() / 86400
            months.append(held / 30.0)
            cost = float(b["all_in_home"]) + grades.get(s["purchase_id"], 0.0)
            profits.append(float(s["net_home"]) - cost)
            days += cost * held
        raw = statistics.median(ratios) if ratios else None
        n = len(ratios)
        bias = min(1.0, (n * raw + SHRINK) / (n + SHRINK)) if raw is not None else 1.0
        mistakes: dict[str, int] = {}
        for m in self.events("MISTAKE", as_of):
            mistakes[m["tag"]] = mistakes.get(m["tag"], 0) + 1
        return Calibration(len(profits), bias, raw, statistics.median(overruns) if overruns else 1.0,
                           statistics.median(months) if months else None,
                           sum(p > 0 for p in profits) / len(profits) if profits else None, sum(profits), days, mistakes)

    def grade_counts(self, *, printing_id: str | None = None, company: str = "PSA") -> dict[str, int]:
        """Your own grade outcomes. With a printing, only that printing's; otherwise every submission."""
        buys = {e["id"]: e for e in self.events("PURCHASE")}
        out: dict[str, int] = {}
        for g in self.events("GRADING_RESULT"):
            if g["company"] != company:
                continue
            if printing_id and buys.get(g["purchase_id"], {}).get("printing_id") != printing_id:
                continue
            out[str(g["grade"])] = out.get(str(g["grade"]), 0) + 1
        return out
