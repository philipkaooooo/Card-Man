"""Persistent evidence and decision store (CLAUDE.md sections 3, 5, 6, 7).

SQLite, append-only by design:
* An observation is written once. Writing the same id with different content is refused,
  because source facts are never rewritten. Later judgements (invalid, cancelled, refunded,
  relisted, duplicate) are separate status events; the original row never changes.
* A decision snapshot holds the exact inputs and outputs of one valuation, keyed by a hash
  of that content, so it can be replayed and checked later.
* Alerts are unique per (listing, snapshot), so one immutable opportunity alerts once.
* Human decisions (YES / WAIT / NO) point at a snapshot. YES authorizes a next human action;
  nothing in this module buys or bids.
* Invalidating a printing (e.g. the identity cache retired it, or a wrong-print finding)
  marks every snapshot that relied on it as invalid, without deleting anything.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

from .economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling, scenarios_from_comps
from .models import EvidenceType, MarketObservation
from .workflow import Action, Decision, DealEvaluation

STORE_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observation (
    id TEXT PRIMARY KEY, evidence_type TEXT NOT NULL, source TEXT NOT NULL, listing_id TEXT,
    source_event_id TEXT, event_time TEXT NOT NULL, capture_time TEXT NOT NULL, synthetic INTEGER NOT NULL,
    data_json TEXT NOT NULL, content_hash TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS obs_listing ON observation(source, listing_id);
CREATE TABLE IF NOT EXISTS observation_printing (obs_id TEXT NOT NULL, printing_id TEXT NOT NULL,
    PRIMARY KEY (obs_id, printing_id));
CREATE INDEX IF NOT EXISTS obs_printing ON observation_printing(printing_id);
CREATE TABLE IF NOT EXISTS observation_status (
    obs_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
    recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot (
    id TEXT PRIMARY KEY, listing_ref TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
    stage TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_printing (snapshot_id TEXT NOT NULL, printing_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, printing_id));
CREATE TABLE IF NOT EXISTS invalidation (
    printing_id TEXT NOT NULL, reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alert (
    listing_ref TEXT NOT NULL, snapshot_id TEXT NOT NULL, sent_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    PRIMARY KEY (listing_ref, snapshot_id));
CREATE TABLE IF NOT EXISTS decision (
    snapshot_id TEXT NOT NULL REFERENCES snapshot(id), action TEXT NOT NULL, rationale TEXT NOT NULL,
    wait_trigger TEXT, decided_at TEXT NOT NULL);
"""


class ImmutableViolation(ValueError):
    pass


def _enc(v):
    if isinstance(v, Decimal):
        return {"$dec": str(v)}
    if isinstance(v, datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, tuple):
        return [_enc(x) for x in v]
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    return v


def _dec(v):
    if isinstance(v, dict):
        if set(v) == {"$dec"}:
            return Decimal(v["$dec"])
        if set(v) == {"$dt"}:
            return datetime.fromisoformat(v["$dt"])
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def canonical(obj) -> str:
    return json.dumps(_enc(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


# Fields that describe when/how we captured a record rather than the source fact itself.
# Re-importing the same fact in a later run changes these, and that is not a rewrite.
_CAPTURE_FIELDS = ("capture_time", "raw_ref")


def _fact(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _CAPTURE_FIELDS}


def _obs_to_dict(o: MarketObservation) -> dict:
    return asdict(o)


def _obs_from_dict(d: dict) -> MarketObservation:
    d = _dec(d)
    d["evidence_type"] = EvidenceType(d["evidence_type"])
    for f in fields(MarketObservation):
        if f.name in ("printing_candidates", "upstream_lineage"):
            d[f.name] = tuple(d[f.name])
    return MarketObservation(**d)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return dt.isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('store_schema_version', ?)", (str(STORE_SCHEMA_VERSION),))

    # -- observations --------------------------------------------------------------------------
    def add_observation(self, o: MarketObservation, *, now: datetime | None = None) -> bool:
        """Insert once. Returns False for an identical re-insert; raises if the content differs."""
        d = _obs_to_dict(o)
        h = content_hash(d)
        row = self.db.execute("SELECT content_hash, data_json FROM observation WHERE id=?", (o.id,)).fetchone()
        if row:
            if row[0] != h and _fact(json.loads(row[1])) != _fact(json.loads(canonical(d))):
                raise ImmutableViolation(f"observation {o.id} already stored with different content; "
                                         "record a status event instead of rewriting the source fact")
            # Same source fact seen again (possibly captured later): keep the first capture.
            return False
        with self.db:
            self.db.execute(
                "INSERT INTO observation VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (o.id, o.evidence_type.value, o.source, o.listing_id, o.source_event_id, _ts(o.event_time),
                 _ts(o.capture_time), int(o.synthetic_fixture), canonical(d), h, _ts(now or _now())))
            self.db.executemany("INSERT INTO observation_printing VALUES (?,?)",
                                [(o.id, p) for p in o.printing_candidates])
        return True

    def set_status(self, obs_id: str, status: str, reason: str, *, actor: str, now: datetime | None = None) -> None:
        if not self.db.execute("SELECT 1 FROM observation WHERE id=?", (obs_id,)).fetchone():
            raise KeyError(obs_id)
        if not reason:
            raise ValueError("a status change needs a reason")
        with self.db:
            self.db.execute("INSERT INTO observation_status VALUES (?,?,?,?,?)",
                            (obs_id, status, reason, actor, _ts(now or _now())))

    def original(self, obs_id: str) -> MarketObservation:
        row = self.db.execute("SELECT data_json FROM observation WHERE id=?", (obs_id,)).fetchone()
        if not row:
            raise KeyError(obs_id)
        return _obs_from_dict(json.loads(row[0]))

    def effective(self, obs_id: str, as_of: datetime | None = None) -> MarketObservation:
        """The original record with the latest status known at `as_of` applied (no leakage)."""
        o = self.original(obs_id)
        q = "SELECT status, reason FROM observation_status WHERE obs_id=?"
        args: list = [obs_id]
        if as_of is not None:
            q += " AND recorded_at<=?"
            args.append(_ts(as_of))
        row = self.db.execute(q + " ORDER BY rowid DESC LIMIT 1", args).fetchone()
        if row:
            o = MarketObservation(**{**o.__dict__, "status": row[0], "status_reason": row[1]})
        return o

    def status_history(self, obs_id: str) -> list[tuple[str, str, str, str]]:
        return self.db.execute("SELECT status, reason, actor, recorded_at FROM observation_status WHERE obs_id=?"
                               " ORDER BY rowid", (obs_id,)).fetchall()

    def observations_for(self, printing_id: str, *, as_of: datetime) -> list[MarketObservation]:
        """Observations captured by `as_of` that name this printing, with status as known then."""
        ids = [r[0] for r in self.db.execute(
            "SELECT o.id FROM observation o JOIN observation_printing p ON p.obs_id=o.id"
            " WHERE p.printing_id=? AND o.capture_time<=? ORDER BY o.event_time", (printing_id, _ts(as_of)))]
        return [self.effective(i, as_of) for i in ids]

    # -- snapshots -----------------------------------------------------------------------------
    def save_snapshot(self, *, listing_ref: str, payload: dict, printing_ids: list[str],
                      stage: str | None = None, expires_at: datetime | None = None,
                      now: datetime | None = None) -> str:
        sid = content_hash({"listing_ref": listing_ref, "payload": payload})[:24]
        row = self.db.execute("SELECT 1 FROM snapshot WHERE id=?", (sid,)).fetchone()
        if row:
            return sid   # same content, same snapshot
        with self.db:
            self.db.execute("INSERT INTO snapshot VALUES (?,?,?,?,?,?)",
                            (sid, listing_ref, _ts(now or _now()), _ts(expires_at) if expires_at else None, stage,
                             canonical(payload)))
            self.db.executemany("INSERT OR IGNORE INTO snapshot_printing VALUES (?,?)",
                                [(sid, p) for p in printing_ids])
        return sid

    def snapshot(self, sid: str) -> dict:
        row = self.db.execute("SELECT listing_ref, payload_json FROM snapshot WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError(sid)
        payload = _dec(json.loads(row[1]))
        if content_hash({"listing_ref": row[0], "payload": payload})[:24] != sid:
            raise ImmutableViolation(f"snapshot {sid} content does not match its id")
        return payload

    def invalidate_printing(self, printing_id: str, reason: str, *, now: datetime | None = None) -> list[str]:
        """Record that a printing identity is no longer trusted; returns the affected snapshot ids."""
        with self.db:
            self.db.execute("INSERT INTO invalidation VALUES (?,?,?)", (printing_id, reason, _ts(now or _now())))
        return [r[0] for r in self.db.execute("SELECT snapshot_id FROM snapshot_printing WHERE printing_id=?",
                                              (printing_id,))]

    def invalidate_retired(self, cache) -> list[str]:
        """Invalidate every printing the identity cache has retired and that a snapshot used."""
        affected = []
        for (pid,) in self.db.execute("SELECT DISTINCT printing_id FROM snapshot_printing").fetchall():
            if cache.is_retired(pid) and not self.db.execute(
                    "SELECT 1 FROM invalidation WHERE printing_id=?", (pid,)).fetchone():
                affected += self.invalidate_printing(pid, f"retired in identity source at {cache.revision()}")
        return affected

    def snapshot_validity(self, sid: str) -> tuple[bool, list[str]]:
        reasons = [f"{pid}: {why}" for pid, why in self.db.execute(
            "SELECT i.printing_id, i.reason FROM invalidation i JOIN snapshot_printing s"
            " ON s.printing_id=i.printing_id WHERE s.snapshot_id=?", (sid,))]
        return (not reasons), reasons

    # -- alerts and decisions ------------------------------------------------------------------
    def maybe_alert(self, ev: DealEvaluation, sid: str, now: datetime) -> bool:
        from .workflow import Stage
        if ev.stage != Stage.QUALIFIED or now >= ev.expires_at:
            return False
        ok, _ = self.snapshot_validity(sid)
        if not ok:
            return False
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO alert VALUES (?,?,?,?)",
                                  (ev.listing_ref, sid, _ts(now), _ts(ev.expires_at)))
        return cur.rowcount == 1

    def expired_alerts(self, now: datetime) -> list[tuple[str, str]]:
        return self.db.execute("SELECT listing_ref, snapshot_id FROM alert WHERE expires_at<=?",
                               (_ts(now),)).fetchall()

    def record_decision(self, d: Decision) -> None:
        if not self.db.execute("SELECT 1 FROM snapshot WHERE id=?", (d.deal_snapshot_id,)).fetchone():
            raise KeyError(f"no snapshot {d.deal_snapshot_id}: decisions must point at what was known")
        ok, why = self.snapshot_validity(d.deal_snapshot_id)
        if d.action == Action.YES and not ok:
            raise ValueError(f"YES refused: snapshot relies on invalidated identity ({'; '.join(why)})")
        with self.db:
            self.db.execute("INSERT INTO decision VALUES (?,?,?,?,?)",
                            (d.deal_snapshot_id, d.action.value, d.rationale, d.wait_trigger, _ts(d.timestamp)))

    # -- listings for review -------------------------------------------------------------------
    def snapshots(self, limit: int = 200) -> list[dict]:
        """Newest first, with the latest human decision and whether an alert went out."""
        rows = self.db.execute(
            "SELECT s.id, s.listing_ref, s.created_at, s.expires_at, s.stage,"
            " (SELECT action FROM decision d WHERE d.snapshot_id=s.id ORDER BY d.rowid DESC LIMIT 1),"
            " EXISTS(SELECT 1 FROM alert a WHERE a.snapshot_id=s.id)"
            " FROM snapshot s ORDER BY s.created_at DESC, s.rowid DESC LIMIT ?", (limit,)).fetchall()
        keys = ("id", "listing_ref", "created_at", "expires_at", "stage", "decision", "alerted")
        return [dict(zip(keys, r)) for r in rows]

    def snapshot_meta(self, sid: str) -> dict:
        row = self.db.execute("SELECT listing_ref, created_at, expires_at, stage FROM snapshot WHERE id=?",
                              (sid,)).fetchone()
        if not row:
            raise KeyError(sid)
        return dict(zip(("listing_ref", "created_at", "expires_at", "stage"), row))

    def observation_list(self, printing_id: str | None = None, *, as_of: datetime | None = None,
                         limit: int = 500) -> list[MarketObservation]:
        """Stored observations (newest sale first), with status as known at `as_of`."""
        if printing_id:
            q = ("SELECT o.id FROM observation o JOIN observation_printing p ON p.obs_id=o.id"
                 " WHERE p.printing_id=? ORDER BY o.event_time DESC LIMIT ?")
            args: tuple = (printing_id, limit)
        else:
            q, args = "SELECT id FROM observation ORDER BY event_time DESC LIMIT ?", (limit,)
        return [self.effective(r[0], as_of) for r in self.db.execute(q, args).fetchall()]

    def printing_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT printing_id FROM observation_printing ORDER BY printing_id")]

    def decisions(self, sid: str) -> list[tuple]:
        return self.db.execute("SELECT action, rationale, wait_trigger, decided_at FROM decision WHERE snapshot_id=?"
                               " ORDER BY rowid", (sid,)).fetchall()


# -- replay -------------------------------------------------------------------------------------

def valuation_inputs(comp_prices_home: list[Decimal], inp: CeilingInputs | None) -> dict:
    """What a snapshot must hold to recompute its valuation exactly."""
    return {"comp_prices_home": list(comp_prices_home),
            "ceiling_inputs": None if inp is None else {
                "acquisition": asdict(inp.acquisition), "exit": asdict(inp.exit),
                "target_return": inp.target_return, "holding_months": inp.holding_months,
                "capital_cost_monthly": inp.capital_cost_monthly}}


def compute_valuation(inputs: dict) -> dict:
    sc = scenarios_from_comps(inputs["comp_prices_home"])
    out = {"scenarios": asdict(sc), "buy_ceiling": None, "calc_version": None}
    ci = inputs.get("ceiling_inputs")
    if ci is not None and sc.conservative is not None:
        res = buy_ceiling(CeilingInputs(sc.conservative, AcquisitionCosts(**ci["acquisition"]),
                                        ExitCosts(**ci["exit"]), ci["target_return"], ci["holding_months"],
                                        ci["capital_cost_monthly"]))
        out.update(buy_ceiling=res.buy_ceiling, calc_version=res.calc_version)
    return out


def replay(store: Store, sid: str) -> dict:
    """Recompute a stored valuation from its stored inputs and compare with what was recorded."""
    snap = store.snapshot(sid)
    again = compute_valuation(snap["valuation_inputs"])
    recorded = snap["valuation"]
    valid, why = store.snapshot_validity(sid)
    return {"snapshot_id": sid, "matches": canonical(again) == canonical(recorded),
            "recorded": recorded, "recomputed": again, "identity_valid": valid, "invalidations": why}


# -- command line -------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """Maintain the evidence store between runs.

    import  CSV                 record manual-import rows (same template as the slice)
    status  OBS STATUS REASON   record a later finding: CANCELLED, REFUNDED, RELISTED, INVALID, DUPLICATE, VALID
    comps   PRINTING            the comparables the store would give for a printing at --as-of
    replay  SNAPSHOT            recompute a stored valuation and check it
    decide  SNAPSHOT YES|WAIT|NO RATIONALE [--wait-trigger T]
    """
    import argparse

    from .evidence import import_manual_csv, select_comps

    ap = argparse.ArgumentParser(prog="python -m cardman.store", description=main.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default="data/store/evidence.sqlite")
    ap.add_argument("--now", help="ISO time, default now (UTC)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import"); p.add_argument("csv")
    p = sub.add_parser("status"); p.add_argument("obs_id"); p.add_argument("status"); p.add_argument("reason")
    p.add_argument("--actor", default="manual")
    p = sub.add_parser("comps"); p.add_argument("printing_id"); p.add_argument("--as-of")
    p.add_argument("--grade-company"); p.add_argument("--grade"); p.add_argument("--condition")
    p.add_argument("--include-synthetic", action="store_true")
    p = sub.add_parser("replay"); p.add_argument("snapshot_id")
    p = sub.add_parser("decide"); p.add_argument("snapshot_id"); p.add_argument("action", choices=[a.value for a in Action])
    p.add_argument("rationale"); p.add_argument("--wait-trigger")
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now) if a.now else _now()
    st = Store(a.store)
    out: dict
    if a.cmd == "import":
        rows = import_manual_csv(Path(a.csv), now)
        added = [o.id for o in rows if st.add_observation(o, now=now)]
        out = {"rows": len(rows), "added": len(added), "already_stored": len(rows) - len(added),
               "synthetic": sum(o.synthetic_fixture for o in rows)}
    elif a.cmd == "status":
        st.set_status(a.obs_id, a.status.upper(), a.reason, actor=a.actor, now=now)
        out = {"obs_id": a.obs_id, "history": st.status_history(a.obs_id)}
    elif a.cmd == "comps":
        as_of = datetime.fromisoformat(a.as_of) if a.as_of else now
        pool = st.observations_for(a.printing_id, as_of=as_of)
        comps, rejected = select_comps(pool, a.printing_id, as_of=as_of, grade_company=a.grade_company,
                                       grade=a.grade, raw_condition=a.condition,
                                       include_synthetic=a.include_synthetic)
        out = {"printing_id": a.printing_id, "as_of": as_of.isoformat(), "pool": len(pool),
               "comps": [{"id": c.id, "date": c.event_time.isoformat(), "type": c.evidence_type.value,
                          "amount": str(c.original_amount), "currency": c.currency, "url": c.source_url,
                          "synthetic": c.synthetic_fixture} for c in comps],
               "rejected": rejected}
    elif a.cmd == "replay":
        out = replay(st, a.snapshot_id)
    else:
        st.record_decision(Decision(a.snapshot_id, Action(a.action), a.rationale, now, a.wait_trigger))
        out = {"snapshot_id": a.snapshot_id, "decisions": st.decisions(a.snapshot_id)}
    print(json.dumps(_enc(out), indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
