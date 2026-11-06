"""The continuous loop: collect, remember, analyze only what changed, escalate only what earns it.

usage:
  python -m cardman.engine once [--store PATH]
  python -m cardman.engine run --interval 900
  python -m cardman.engine confirm KEY --printed-code LOB-EN001 --rarity "Ultra Rare" --edition 1st --product "..." --authentic yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import pricing
from .evidence import RawEvidenceStore
from .fx_store import FxStore, rate_rows
from .identity import Catalog
from .identity_cache import IdentityCache
from .intel import analyze
from .ledger import Ledger
from .market_sources import EbayAdapter, InboxAdapter, RawListing, YgoprodeckReference, from_record, market_for
from .models import COMP_ELIGIBLE, EvidenceType, IdentityStatus
from .opportunity import ESCALATED, OBSERVE, OPEN, QUALIFIED, REJECTED, SHELVED, SPECTATE, Opportunity, PriceBook, costs_for, evaluate_listing
from .slice import run as run_slice
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
GONE_AFTER = timedelta(days=14)     # active listings no route has reported in this long stop being re-analyzed
SIGNAL_WINDOW = timedelta(days=14)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listing (key TEXT PRIMARY KEY, source TEXT, listing_id TEXT, market TEXT, title TEXT, url TEXT, seller TEXT,
  kind TEXT, price TEXT, currency TEXT, ends_at TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, fingerprint TEXT, data_json TEXT);
CREATE INDEX IF NOT EXISTS listing_seller ON listing(seller, first_seen);
CREATE TABLE IF NOT EXISTS listing_event (key TEXT, at TEXT, kind TEXT, old TEXT, new TEXT);
CREATE INDEX IF NOT EXISTS listing_event_key ON listing_event(key, at);
CREATE TABLE IF NOT EXISTS listing_review (key TEXT, at TEXT, actor TEXT, verified_json TEXT);
CREATE TABLE IF NOT EXISTS opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT,
  printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS opp_rank ON opportunity(status, priority);
CREATE TABLE IF NOT EXISTS inbox_seen (hash TEXT PRIMARY KEY, file TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS ocr_cache (path TEXT, mtime REAL, codes_json TEXT, text TEXT, PRIMARY KEY (path, mtime));
CREATE TABLE IF NOT EXISTS market_signal (id TEXT PRIMARY KEY, kind TEXT, printing_id TEXT, at TEXT, detail_json TEXT);
CREATE TABLE IF NOT EXISTS engine_run (at TEXT, data_json TEXT);
"""


def load_profile(path: Path | None = None) -> dict:
    for p in (path, ROOT / "data" / "profile.json", ROOT / "examples" / "cost_profiles_ASSUMED.json"):
        if p and Path(p).exists():
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            d["_assumed"] = Path(p).name.endswith("_ASSUMED.json")
            return d
    raise FileNotFoundError("no cost profile")


@dataclass
class TickReport:
    at: str
    new_listings: int = 0
    price_changes: int = 0
    analyzed: int = 0
    skipped_unchanged: int = 0
    open_opportunities: int = 0
    escalated: int = 0
    rejected: int = 0
    signals: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)


class Engine:
    def __init__(self, store_path: str, *, catalog: Catalog | None = None, catalog_db: str | None = None, profile: dict | None = None,
                 inbox: str | Path | None = None, raw_dir: str | Path | None = None, fx_get=None, reference_get=None, use_reference: bool = True):
        self.store_path = store_path
        self.store = Store(store_path)
        self.db = sqlite3.connect(store_path)
        self.db.executescript(_SCHEMA)
        self.catalog_db = catalog_db or str(ROOT / "data" / "cache" / "identity.sqlite")
        self.catalog = catalog if catalog is not None else self._load_catalog()
        self.profile = profile or load_profile()
        self.fx = FxStore(store_path, get_json=fx_get) if fx_get else FxStore(store_path)
        self.ledger = Ledger(store_path, home=self.profile["home_currency"])
        self.raw = RawEvidenceStore(Path(raw_dir or (ROOT / "data" / "raw")))
        self.inbox = InboxAdapter(inbox or (ROOT / "data" / "inbox"), self.raw, self._seen)
        self.ebay = EbayAdapter(self.raw, list(self.profile.get("ebay_queries") or []), self.catalog)
        self.reference = YgoprodeckReference(self.db, get=reference_get) if use_reference else None
        self.routes = [self.inbox, self.ebay] + ([self.reference] if self.reference else [])

    def _load_catalog(self) -> Catalog:
        cache = IdentityCache(self.catalog_db)
        if not cache.revision():
            raise SystemExit(f"identity cache {self.catalog_db} is empty: run python -m cardman.identity_cache sync")
        self.catalog_rev = cache.revision()
        return cache.catalog()

    def _seen(self, h: str, mark: str | None = None) -> bool:
        if mark is not None:
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO inbox_seen VALUES (?,?,?)", (h, mark, datetime.now(timezone.utc).isoformat()))
            return True
        return self.db.execute("SELECT 1 FROM inbox_seen WHERE hash=?", (h,)).fetchone() is not None

    # -- memory ---------------------------------------------------------------------------------------------------
    def remember(self, r: RawListing, now: datetime, rep: TickReport) -> None:
        row = self.db.execute("SELECT price, kind FROM listing WHERE key=?", (r.key,)).fetchone()
        data = {"description": r.description, "shipping": str(r.shipping) if r.shipping is not None else None, "photos": list(r.photos),
                "grade_company": r.grade_company, "grade": r.grade, "condition": r.condition, "verified": r.verified, "printing_id": r.printing_id,
                "event_time": r.event_time.isoformat() if r.event_time else None, "raw_ref": r.raw_ref, "synthetic": r.synthetic, "bids": r.bids}
        ended = (r.event_time or now).isoformat() if r.kind in ("sold", "accepted_offer", "transaction", "unsold") else None
        with self.db:
            if row is None:
                self.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (r.key, r.source, r.listing_id, r.market, r.title, r.url, r.seller, r.kind, str(r.price), r.currency,
                                 r.ends_at.isoformat() if r.ends_at else None, now.isoformat(), now.isoformat(), ended, r.fingerprint(),
                                 json.dumps(data, ensure_ascii=False)))
                self.db.execute("INSERT INTO listing_event VALUES (?,?,?,?,?)", (r.key, now.isoformat(), "FIRST_SEEN", None, f"{r.price} {r.currency}"))
                rep.new_listings += 1
            else:
                if Decimal(row[0]) != r.price:
                    self.db.execute("INSERT INTO listing_event VALUES (?,?,?,?,?)", (r.key, now.isoformat(), "PRICE_CHANGE", row[0], str(r.price)))
                    rep.price_changes += 1
                if row[1] != r.kind:
                    self.db.execute("INSERT INTO listing_event VALUES (?,?,?,?,?)", (r.key, now.isoformat(), "STATE_CHANGE", row[1], r.kind))
                self.db.execute("""UPDATE listing SET title=?, url=?, seller=?, kind=?, price=?, currency=?, ends_at=?, last_seen=?, ended_at=COALESCE(ended_at, ?),
                                   fingerprint=?, data_json=? WHERE key=?""",
                                (r.title, r.url, r.seller, r.kind, str(r.price), r.currency, r.ends_at.isoformat() if r.ends_at else None, now.isoformat(), ended,
                                 r.fingerprint(), json.dumps(data, ensure_ascii=False), r.key))

    def _record_observation(self, r: RawListing, candidates: list[str], now: datetime, rep: TickReport) -> None:
        try:
            self.store.add_observation(r.to_observation(tuple(candidates), now), now=now)
        except ValueError as x:
            rep.errors.append(f"{r.key}: {x}")

    def _printing_known(self, pid: str) -> bool:
        base = pid.split("|")[0]
        return any(p.id == base for p in self.catalog.printings) if not hasattr(self, "_ids") else base in self._ids

    def reviews(self, key: str) -> dict:
        out: dict = {}
        for (v,) in self.db.execute("SELECT verified_json FROM listing_review WHERE key=? ORDER BY at", (key,)):
            out.update(json.loads(v))
        return out

    def confirm(self, key: str, verified: dict, *, actor: str, now: datetime) -> None:
        """A person confirmed something on the card itself. This is what lets identity resolve."""
        if not self.db.execute("SELECT 1 FROM listing WHERE key=?", (key,)).fetchone():
            raise KeyError(key)
        clean = {k: v for k, v in verified.items() if v not in (None, "")}
        with self.db:
            self.db.execute("INSERT INTO listing_review VALUES (?,?,?,?)", (key, now.isoformat(), actor, json.dumps(clean, ensure_ascii=False)))

    def listing_from_row(self, key: str) -> RawListing:
        c = self.db.execute("SELECT source, listing_id, market, title, url, seller, kind, price, currency, ends_at, data_json FROM listing WHERE key=?", (key,)).fetchone()
        d = json.loads(c[10])
        return RawListing(source=c[0], listing_id=c[1], market=c[2], title=c[3], url=c[4] or "", seller=c[5], kind=c[6], price=Decimal(c[7]), currency=c[8],
                          ends_at=datetime.fromisoformat(c[9]) if c[9] else None, description=d.get("description") or "",
                          shipping=Decimal(d["shipping"]) if d.get("shipping") else None, photos=tuple(d.get("photos") or ()),
                          grade_company=d.get("grade_company"), grade=d.get("grade"), condition=d.get("condition"), verified=d.get("verified") or {},
                          printing_id=d.get("printing_id"), event_time=datetime.fromisoformat(d["event_time"]) if d.get("event_time") else None,
                          raw_ref=d.get("raw_ref"), synthetic=d.get("synthetic", False), bids=d.get("bids"))

    # -- analysis -------------------------------------------------------------------------------------------------
    def _ocr(self, r: RawListing) -> tuple[str, ...]:
        from . import vision
        paths = [p for p in r.photos if Path(p).is_file()]
        if not paths or not vision.available()[0]:
            return ()
        codes: list[str] = []
        todo = []
        for p in paths:
            m = Path(p).stat().st_mtime
            row = self.db.execute("SELECT codes_json FROM ocr_cache WHERE path=? AND mtime=?", (p, m)).fetchone()
            if row:
                codes += json.loads(row[0])
            else:
                todo.append(p)
        if todo:
            for reading in vision.read_images(todo):
                if reading.error:
                    continue
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO ocr_cache VALUES (?,?,?,?)",
                                    (reading.path, Path(reading.path).stat().st_mtime, json.dumps(reading.codes), reading.text))
                codes += reading.codes
        return tuple(dict.fromkeys(codes))

    def _evidence_version(self, ids: list[str]) -> str:
        if not ids:
            return "0"
        ph = ",".join("?" * len(ids[:500]))
        return "/".join(str(x) for x in self.db.execute(
            f"SELECT COUNT(*), MAX(o.recorded_at) FROM observation o JOIN observation_printing p ON p.obs_id=o.id WHERE p.printing_id IN ({ph})",
            ids[:500]).fetchone())

    def analyze_listing(self, key: str, book: PriceBook, now: datetime, rep: TickReport, *, force: bool = False) -> Opportunity | None:
        r = self.listing_from_row(key)
        verified = {**r.verified, **self.reviews(key)}
        authenticity = verified.pop("authenticity_ok", None)
        an = analyze(r.title, self.catalog, description=r.description, verified=verified, ocr_codes=self._ocr(r))
        ids = [p.id for p in an.identity.candidates]
        fp = hashlib.sha256(json.dumps([r.fingerprint(), verified, authenticity, ids[:500], self._evidence_version(ids),
                                        now.date().isoformat() if r.ends_at else ""], sort_keys=True, default=str).encode()).hexdigest()[:16]
        prev = self.db.execute("SELECT analysis_fp, created_at, snapshot_id FROM opportunity WHERE key=?", (key,)).fetchone()
        if prev and prev[0] == fp and not force:
            rep.skipped_unchanged += 1
            return None
        rep.analyzed += 1
        op = evaluate_listing(r, an, book, self.catalog, self.ledger, now)
        snapshot = prev[2] if prev else None
        escalated = self._escalate(r, an, op, verified, authenticity, book, now, rep)
        if escalated:
            snapshot, op.stage, op.status = escalated
            rep.escalated += 1
        if op.status == REJECTED:
            rep.rejected += 1
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (key, op.stage, op.status, op.priority, op.expected_profit, op.evpi, json.dumps(op.kinds), op.printing_id, snapshot, fp,
                             json.dumps(op.to_dict(), ensure_ascii=False, default=str), prev[1] if prev else now.isoformat(), now.isoformat()))
        return op

    def _escalate(self, r: RawListing, an, op: Opportunity, verified: dict, authenticity, book: PriceBook, now: datetime, rep: TickReport):
        """Hand a defensible, economic listing to the original workflow: it writes the snapshot, gates and any alert."""
        p = an.identity.printing
        if not p or op.status == REJECTED or r.kind not in ("ask", "bid", "auction"):
            return None
        val = book.value(p.id, r.grade_company, r.grade)
        if len(val.sales) < int(self.profile.get("min_comps", 3)) or not val.estimate:
            return None
        scenario = next((s for s in op.scenarios if s.printing_id == p.id), None)
        if not scenario or scenario.max_buy is None or op.ask_home is None or op.ask_home > scenario.max_buy:
            return None
        home = self.profile["home_currency"]
        moments = [c.event_time for c in val.comps] + [now]
        pairs = sorted({(c.currency, home) for c in val.comps} | {(r.currency, home)})
        ship = book.to_home(r.shipping, r.currency, now) if r.shipping else Decimal(0)
        acq, ex = costs_for(self.profile, r.market, ship)
        listing = {
            "listing_ref": r.key, "catalog_db": self.catalog_db, "home_currency": home, "source_url": r.url,
            "signals": {"title": r.title, **{k: v for k, v in verified.items()}},
            "ask": {"amount": str(r.price), "currency": r.currency}, "last_seen": now.isoformat(), "authenticity_ok": authenticity,
            "grade_company": r.grade_company, "grade": r.grade, "fx": rate_rows(self.fx.rates_for(pairs, moments)),
            "costs": {"target_return": str(self.profile["target_return"]), "capital_cost_monthly": str(self.profile["capital_cost_monthly"]),
                      "holding_months": str(round(min(scenario.months_to_sell or 2, 36), 2)),
                      "acquisition": {k: str(v) for k, v in acq.__dict__.items() if v}, "exit": {k: str(v) for k, v in ex.__dict__.items() if v}},
        }
        try:
            report = run_slice(listing, None, now, False, self.store_path, catalog=self.catalog)
        except Exception as x:   # stale FX, malformed cost: stay in Spectate and say why
            rep.errors.append(f"{r.key}: valuation failed: {x}")
            op.reasons.append(f"Valuation failed: {x}")
            return None
        stage = report["status"]["stage"]
        op.reasons.append(f"Escalated: workflow stage {stage}; blocked by: {', '.join(report['status']['blocked_by']) or 'nothing'}.")
        return report["status"]["stored_snapshot_id"], (QUALIFIED if stage == QUALIFIED else SPECTATE), ESCALATED

    # -- market-wide ----------------------------------------------------------------------------------------------
    def scan_market(self, book: PriceBook, now: datetime) -> int:
        added = 0
        P = self.profile

        def emit(kind, pid, detail):
            nonlocal added
            sid = f"{kind}:{pid}:{now.date().isoformat()}"
            with self.db:
                added += self.db.execute("INSERT OR IGNORE INTO market_signal VALUES (?,?,?,?,?)",
                                         (sid, kind, pid, now.isoformat(), json.dumps(detail, ensure_ascii=False, default=str))).rowcount

        for pid in self.store.printing_ids():
            sold = [o for o in self.store.observations_for(pid, as_of=now)
                    if o.evidence_type in COMP_ELIGIBLE and o.status == "VALID" and not o.synthetic_fixture and o.printing_candidates == (pid,)
                    and not o.is_lot and o.grade_company is None]
            if len(sold) < 3:
                continue
            homes = []
            for o in sold:
                try:
                    homes.append((o, float(book.to_home(o.original_amount, o.currency, o.event_time))))
                except Exception:
                    continue
            recent = [h for o, h in homes if now - o.event_time <= SIGNAL_WINDOW]
            base = [h for o, h in homes if SIGNAL_WINDOW < now - o.event_time <= timedelta(days=104)]
            if len(recent) >= 2 and len(base) >= 3:
                chg = statistics.median(recent) / statistics.median(base) - 1
                if abs(chg) >= 0.25:
                    emit("PRICE_SHIFT", pid, {"change": round(chg, 3), "recent_n": len(recent), "base_n": len(base),
                                              "text": f"Median sale moved {chg:+.0%} in 14 days against the prior 90 ({len(recent)} vs {len(base)} sales)."})
                rate_recent, rate_base = len(recent) / 14, len(base) / 90
                if len(recent) >= 3 and rate_recent >= 3 * rate_base:
                    emit("VOLUME_SPIKE", pid, {"text": f"{len(recent)} sales in 14 days, {rate_recent / rate_base:.1f}x the usual pace."})
            by_market: dict[str, list[float]] = {}
            for o, h in homes:
                if now - o.event_time <= timedelta(days=180):
                    by_market.setdefault(market_for(o.source, o.currency), []).append(h)
            for buy_mkt, buys in by_market.items():
                for sell_mkt, sells in by_market.items():
                    if buy_mkt == sell_mkt or len(buys) < 3 or len(sells) < 3:
                        continue
                    acq, ex = costs_for(P, buy_mkt)
                    b, s = statistics.median(buys), statistics.median(sells)
                    profit = float(ex.net(Decimal(str(round(s, 2))))) - float(acq.all_in(Decimal(str(round(b, 2)))))
                    if profit > float(acq.all_in(Decimal(str(round(b, 2))))) * float(P["target_return"]):
                        emit(f"REGIONAL_{buy_mkt}_TO_{sell_mkt}", pid, {"buy_median": b, "sell_median": s, "profit": profit,
                             "text": f"Sold median {b:,.0f} in {buy_mkt} and {s:,.0f} in {sell_mkt}: {profit:,.0f} after costs ({len(buys)} and {len(sells)} sales)."})
        since = (now - timedelta(days=7)).isoformat()
        for seller, n in self.db.execute("SELECT seller, COUNT(*) FROM listing WHERE seller IS NOT NULL AND first_seen>=? GROUP BY seller HAVING COUNT(*)>=5", (since,)):
            emit("SELLER_ACTIVITY", seller, {"text": f"{seller} listed {n} items in 7 days: a collection may be coming to market.", "n": n})
        return added

    # -- the tick -------------------------------------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> TickReport:
        started = time.monotonic()
        now = now or datetime.now(timezone.utc)
        rep = TickReport(at=now.isoformat())
        home = self.profile["home_currency"]
        for base in ("USD", "JPY", "TWD"):
            if base != home:
                try:
                    self.fx.sync_latest(base, (home,))
                except Exception as x:
                    rep.errors.append(f"FX {base}/{home}: {x}")
        new: list[RawListing] = []
        for route in (self.inbox, self.ebay):
            rep.routes.append(asdict(route.status()))
            try:
                got, errs = route.poll(now)
                new += got
                rep.errors += errs
            except Exception as x:   # one route failing must not stop the others, or read as "no supply"
                rep.errors.append(f"{route.name}: {x}")
        if self.reference:
            rep.routes.append(asdict(self.reference.status()))
        for r in new:
            self.remember(r, now, rep)
            self._take_evidence(r, now, rep)
        cal = self.ledger.calibration(now)
        book = PriceBook(self.store, self.fx, self.profile, now, bias=cal.resale_bias, reference=self.reference)
        cutoff = (now - GONE_AFTER).isoformat()
        keys = [k for (k,) in self.db.execute("SELECT key FROM listing WHERE ended_at IS NULL AND last_seen>=? AND (ends_at IS NULL OR ends_at>?)",
                                              (cutoff, now.isoformat()))]
        for k in keys:
            try:
                self.analyze_listing(k, book, now, rep)
            except Exception as x:
                rep.errors.append(f"{k}: {type(x).__name__}: {x}")
        rep.errors += list(dict.fromkeys(book.notes))[:20]
        rep.signals = self.scan_market(book, now)
        rep.open_opportunities = self.db.execute("SELECT COUNT(*) FROM opportunity WHERE status=?", (OPEN,)).fetchone()[0]
        rep.seconds = round(time.monotonic() - started, 3)
        with self.db:
            self.db.execute("INSERT INTO engine_run VALUES (?,?)", (now.isoformat(), json.dumps(asdict(rep), ensure_ascii=False)))
        return rep

    def _take_evidence(self, r: RawListing, now: datetime, rep: TickReport) -> None:
        """Sold rows only name a printing when you named it or the card's evidence resolves it. Others stay observations of a family."""
        verified = {**r.verified, **self.reviews(r.key)}
        verified.pop("authenticity_ok", None)
        if r.printing_id:
            base = r.printing_id.split("|")[0]
            if not any(p.id == base for p in self.catalog.printings):
                rep.errors.append(f"{r.key}: unknown printing_id {r.printing_id}")
                return
            cands = [r.printing_id]
        else:
            an = analyze(r.title, self.catalog, description=r.description, verified=verified)
            cands = [p.id for p in an.identity.candidates][:200]
        self._record_observation(r, cands, now, rep)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.engine")
    ap.add_argument("cmd", choices=["once", "run", "confirm"])
    ap.add_argument("key", nargs="?")
    ap.add_argument("--store", default=str(ROOT / "data" / "store" / "evidence.sqlite"))
    ap.add_argument("--interval", type=int, default=900)
    for f in ("printed-code", "rarity", "edition", "product", "language", "authentic"):
        ap.add_argument(f"--{f}")
    a = ap.parse_args(argv)
    eng = Engine(a.store)
    if a.cmd == "confirm":
        if not a.key:
            ap.error("confirm needs a listing key")
        eng.confirm(a.key, {"printed_set_code": a.printed_code, "verified_rarity": a.rarity, "verified_edition": a.edition, "verified_product": a.product,
                            "card_language": a.language, "authenticity_ok": {"yes": True, "no": False}.get(a.authentic or "")},
                    actor="cli", now=datetime.now(timezone.utc))
    while True:
        print(json.dumps(asdict(eng.tick()), indent=1, ensure_ascii=False))
        if a.cmd != "run":
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
