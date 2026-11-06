"""Private learning: what actually happened to your decisions. Append-only; corrections are new events.

Calibration comes only from your own completed outcomes. It can lower future resale estimates; it never raises them
above what the sales show, and a few results move it only a little.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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


LEDGER_TO_OUTCOME = {"NEGOTIATION": "OFFER", "REJECTED": "PASSED", "PURCHASE": "PURCHASE", "GRADING_RESULT": "GRADED",
                     "SALE": "SOLD", "MISTAKE": "MISTAKE"}
_AMOUNT = {"PURCHASE": "price_home", "SALE": "gross_home", "GRADING_RESULT": "cost_home", "NEGOTIATION": "asked_home"}


def _plain(d: dict) -> dict:
    return {k: (str(v) if isinstance(v, float) else v) for k, v in d.items()}


class Ledger:
    """The engine's view of your outcomes. Events live in the store's `outcome` table, the same rows the Results page
    writes, so a purchase recorded in either place counts in both. Amounts here are in `home` currency."""

    def __init__(self, path: Path | str, home: str = "TWD"):
        from .store import Store
        self.store = Store(path)
        self.db = self.store.db
        self.home = home

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
        position = data.get("purchase_id") or (eid if kind == "PURCHASE" else (listing_key or eid))
        amt = data.get(_AMOUNT.get(kind, ""))
        body = {**data, "ledger_kind": kind, "ledger_id": eid, "listing_key": listing_key}
        if kind == "SALE":      # so the Results page computes the same net
            body["fees"] = str(Decimal(str(data["gross_home"])) - Decimal(str(data["net_home"])))
        if kind == "MISTAKE":
            body.setdefault("text", f"{data['tag']}: {data['what']}")
        if kind == "REJECTED":
            body.setdefault("text", data["reason"])
        self.store.add_outcome(position, LEDGER_TO_OUTCOME[kind], at,
                               amount=Decimal(str(amt)) if amt not in (None, "") else None,
                               currency=self.home if amt not in (None, "") else None,
                               printing_id=data.get("printing_id"), data=_plain(body), now=now or at)
        return eid

    def get(self, eid: str) -> dict | None:
        return next((e for e in self.events() if e["id"] == eid), None)

    def events(self, kind: str | None = None, as_of: datetime | None = None, limit: int = 1000) -> list[dict]:
        out = []
        native_positions = set()
        for r in self.store.outcomes():
            d = r["data"]
            if "ledger_kind" not in d:
                continue
            native_positions.add(r["position"])
            ev = {k: v for k, v in d.items() if k not in ("ledger_kind", "ledger_id", "listing_key", "fees", "text")}
            out.append({"id": d["ledger_id"], "kind": d["ledger_kind"], "at": r["at"].isoformat(),
                        "listing_key": d.get("listing_key"), "printing_id": r["printing_id"], **ev})
        out += self._from_results_page(native_positions, as_of)
        if kind:
            out = [e for e in out if e["kind"] == kind]
        if as_of:
            out = [e for e in out if datetime.fromisoformat(e["at"]) <= as_of]
        return sorted(out, key=lambda e: e["at"], reverse=True)[:limit]

    def _from_results_page(self, skip: set, as_of: datetime | None) -> list[dict]:
        """Positions entered on the Results page, in home currency with stored FX."""
        from .outcomes import build_positions
        from datetime import timezone
        out = []
        for p in build_positions(self.store, home=self.home, now=as_of or datetime.now(timezone.utc)):
            if p.name in skip or p.bought_at is None or p.fx_problems:
                continue
            listing = None
            if p.snapshot_id:
                try:
                    listing = self.store.snapshot_meta(p.snapshot_id)["listing_ref"]
                except KeyError:
                    pass
            buy = {"id": p.name, "kind": "PURCHASE", "at": p.bought_at.isoformat(), "listing_key": listing,
                   "printing_id": p.printing_id, "price_home": float(p.cost), "all_in_home": float(p.cost)}
            if p.predicted_mid is not None:
                buy["predicted_resale_home"] = float(p.predicted_mid)
            out.append(buy)
            if p.sold_at and p.sale_gross is not None:
                out.append({"id": f"{p.name}:sale", "kind": "SALE", "at": p.sold_at.isoformat(), "listing_key": listing,
                            "printing_id": p.printing_id, "purchase_id": p.name, "gross_home": float(p.sale_gross),
                            "net_home": float(p.proceeds)})
            if p.grade:
                co, _, gr = p.grade.partition(" ")
                out.append({"id": f"{p.name}:grade", "kind": "GRADING_RESULT", "at": p.bought_at.isoformat(),
                            "listing_key": listing, "printing_id": p.printing_id, "purchase_id": p.name,
                            "company": co, "grade": gr, "cost_home": 0.0})   # cost already in the position's cost
        return out

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
