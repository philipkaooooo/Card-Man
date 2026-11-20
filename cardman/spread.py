"""Spread scanner: what is for sale now against what that exact version is worth, after every cost.

    python -m cardman.spread "LOB-001 Dark Magician" "SDK-001 Blue-Eyes White Dragon"

Legitimate sources only:
  asks    eBay's official Browse API (the same client the engine uses; keys from the git-ignored .env)
  value   real sold prices CardMan has recorded for that exact version, and TCGplayer's market price (TCGCSV, from the
          master card list) shown beside it
Each listing goes through the engine's own identification and pricing, so it gets the same answers the app shows:
  - the exact version (set, number, rarity, edition), with reprints, graded cards, lots and fakes labelled, never
    pooled with the original;
  - "total to you" = price + shipping + your buying costs and currency conversion (the buy side);
  - "spread after costs" = what it usually sells for, minus your selling fees and shipping (the sell side), minus the
    total to you. Fees are never taken off the price you pay.
It reads a temporary copy of the store, so a scan never changes what the app holds. Seller names are never shown.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .market_sources import RawListing

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "store" / "evidence.sqlite"


@dataclass
class Row:
    title: str
    version: str
    decision: str
    reason: str
    total: float | None          # home currency
    value: float | None
    n_sales: int
    spread: float | None
    tcgplayer: str               # TCGplayer's market price line (USD), '' when none
    label: str = ""               # graded / lot / reprint / not this card


@dataclass
class Report:
    query: str
    game: str
    rows: list[Row] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _tcgplayer_market(pid: str | None) -> str:
    """TCGplayer's latest market price (USD) for this exact version, from the master card list. When the edition is
    not known, the price of each edition is shown, never one picked for it."""
    if not pid:
        return ""
    from .catalog.registry import DB
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        q = ("SELECT m.pid, m.market FROM market_price m WHERE m.source='tcgplayer' AND m.pid {} "
             "AND m.day=(SELECT MAX(day) FROM market_price WHERE pid=m.pid AND source='tcgplayer')")
        row = con.execute(q.format("=?"), (pid,)).fetchone()
        eds = [] if row else con.execute(q.format("LIKE ?") + " ORDER BY m.market", (pid.split("|")[0] + "|ed:%",)).fetchall()
        con.close()
    except sqlite3.Error:
        return ""
    if row and row[1] is not None:
        return f"TCGplayer ${row[1]:,.2f}"
    eds = [(p.rsplit("|ed:", 1)[-1], v) for p, v in eds if v is not None and "|v:" not in p]
    return ("TCGplayer " + " / ".join(f"${v:,.2f} {ed}" for ed, v in eds)) if eds else ""


def _label(r: RawListing, d: dict) -> str:
    kinds = set(d.get("kinds") or [])
    if "LOT" in kinds:
        return "lot"
    if r.grade_company:
        return f"graded {r.grade_company} {r.grade or ''}".strip()
    if any(f.get("kind") in ("REPRINT_OF", "REPRINT") for f in d.get("findings") or []):
        return "reprint"
    if "MISIDENTIFIED" in kinds:
        return "title and card disagree"
    return ""


def ebay_search(engine, query: str, game: str, now: datetime) -> tuple[list[RawListing], list[str]]:
    """Current asks from eBay's official Browse API, exactly as the engine's eBay route reads them."""
    from .market_sources import EbayAdapter
    a = EbayAdapter(engine.raw, [(query, game)], catalog=engine.catalog)
    st = a.status()
    if not st.on:
        return [], [f"eBay: not searched ({st.detail})"]
    return a.poll(now)


def scan(queries: list[str], *, engine=None, search=ebay_search, now: datetime | None = None) -> list[Report]:
    from .games import guess_game
    from .opportunity import PriceBook, evaluate_listing
    from .ui_listing import verdict
    now = now or datetime.now(timezone.utc)
    if engine is None:
        engine = _engine_on_copy()
    cal = engine.ledger.calibration(now)
    book = PriceBook(engine.store, engine.fx, engine.profile, now, bias=cal.resale_bias, reference=engine.reference)
    out = []
    for q in queries:
        game = guess_game(q, engine.pkm)
        rep = Report(q, game)
        try:
            found, errs = search(engine, q, game, now)
        except Exception as x:                                   # a failing source is said out loud, never hidden
            found, errs = [], [f"eBay: failed ({type(x).__name__}: {x})"]
        rep.warnings += errs
        if not found and not errs:
            rep.warnings.append("eBay: no listings found for this search")
        for r in found:
            r.game = game
            try:
                an = engine._analyze(r, {})
                op = evaluate_listing(r, an, book, engine.catalog_for(game), engine.ledger, now)
            except Exception as x:
                rep.warnings.append(f"{r.listing_id}: could not be priced ({type(x).__name__})")
                continue
            d = {**op.to_dict(), "game": game, "title": r.title, "kind": r.kind, "listing_id": r.listing_id,
                 "shipping": str(r.shipping) if r.shipping is not None else None, "grade_company": r.grade_company}
            v = verdict(d, op.status, lambda x, sign=False: "" if x is None else f"{float(x):,.2f}")
            top = v.top or {}
            sc = next((s for s in d.get("scenarios") or [] if s.get("printing_id") == d.get("printing_id")), None) or top
            rep.rows.append(Row(
                title=r.title, version=(sc.get("label") or "not identified") + ("" if d.get("printing_id") else " (probably)"),
                decision=v.code, reason=v.reason, total=d.get("all_in_home"),
                value=float(top["resale_mid"]) if top.get("resale_mid") else None, n_sales=int(top.get("n_sales") or 0),
                spread=float(top["profit_mid"]) if top.get("profit_mid") is not None and top.get("basis") == "sales" else None,
                tcgplayer=_tcgplayer_market(d.get("printing_id") or top.get("printing_id")), label=_label(r, d)))
        order = {"BUY": 0, "CHECK": 1, "WATCH": 2, "PASS": 3}
        rep.rows.sort(key=lambda x: (order.get(x.decision, 9), -(x.spread if x.spread is not None else -1e12)))
        out.append(rep)
    return out


def _engine_on_copy():
    """An engine over a throwaway copy of the store: the scan reads everything the app knows and writes nothing."""
    from .engine import Engine
    tmp = Path(tempfile.mkdtemp(prefix="cardman-spread-"))
    src = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "store.sqlite")
    src.backup(dst)
    src.close(), dst.close()
    return Engine(str(tmp / "store.sqlite"), raw_dir=tmp / "raw")


def render(reports: list[Report], money, home_label: str = "") -> str:
    from .games import NAME
    from .ui_listing import LABEL
    lines = []
    for rep in reports:
        lines.append(f"\n{rep.query}  ({NAME.get(rep.game, rep.game)})")
        for w in rep.warnings:
            lines.append(f"  ! {w}")
        if not rep.rows:
            continue
        priced = [r for r in rep.rows if r.spread is not None and not r.label]
        lines.append(f"  eBay (official API): {len(rep.rows)} listings, {len(priced)} with an exact version and real sales to price against")
        for r in rep.rows[:12]:
            spread = (f"{'+' if r.spread >= 0 else '-'}{money(abs(r.spread))} after costs" if r.spread is not None else "no spread: not enough sales")
            value = f"value {money(r.value)} ({r.n_sales} sales)" if r.value and r.n_sales else (f"guide {money(r.value)}" if r.value else "no value yet")
            tcg = f" · {r.tcgplayer}" if r.tcgplayer else ""
            lines.append(f"  {LABEL.get(r.decision, r.decision):<8} {money(r.total):>10} total | {value}{tcg} | {spread}")
            lines.append(f"           {r.version}" + (f"  [{r.label}]" if r.label else ""))
        if len(rep.rows) > 12:
            lines.append(f"  … and {len(rep.rows) - 12} more")
    lines.append(f"\nAmounts in {home_label}: total = price + shipping + your buying costs; spread = usual sale price minus "
                 "selling fees and shipping, minus the total.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    queries = [a for a in argv if a.strip()]
    if not queries:
        print('usage: python -m cardman.spread "LOB-001 Dark Magician" "SDK-001 Blue-Eyes White Dragon"')
        return 2
    engine = _engine_on_copy()
    now = datetime.now(timezone.utc)
    from .ui_home import Money
    from .ui import _engine_home
    home = _engine_home(engine.store)
    money = Money(engine.store, home, "USD", now)
    print(render(scan(queries, engine=engine, now=now), money, "USD"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
