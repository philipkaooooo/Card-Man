"""One listing through the whole pipeline, printed stage by stage.

This is the compiler front end: parse the text, resolve the printing with every signal available, price each
candidate from real sales of that exact printing and grade, restate comps to the copy's condition, then pass
the scenarios to the same rules the engine uses. Nothing is written to the store; the output is JSON.

    python -m cardman.compile --title "Blue-Eyes White Dragon LOB-EN001 NM" --ask 120 --currency USD
    python -m cardman.compile --title "..." --ask 55 --currency TWD --condition LP --game pkm
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from . import intel
from .engine import ROOT, Engine
from .history import plain_trend, summarize
from .market_sources import RawListing, market_for
from .opportunity import PriceBook, evaluate_listing

UTC = timezone.utc


def _parsed(an) -> dict:
    d = asdict(an.parsed)
    d.pop("text", None)
    return d


def run(title: str, ask, currency: str, *, description: str = "", shipping=None, condition: str | None = None,
        grade_company: str | None = None, grade: str | None = None, game: str = "ygo", store: str | None = None) -> dict:
    now = datetime.now(UTC)
    eng = Engine(str(store or (ROOT / "data" / "store" / "evidence.sqlite")), use_reference=False)
    if game == "pkm":
        if getattr(eng, "pkm", None) is None:
            return {"error": "Pokémon catalog is not synced; run python -m cardman.pkm_catalog"}
        from .pkm_intel import analyze as pkm_analyze
        an = pkm_analyze(title, eng.pkm, description=description)
    else:
        an = intel.analyze(title, eng.catalog, description=description)
    parsed = getattr(an, "parsed", None)                 # the copy's own words: condition and grade when not passed
    condition = condition or getattr(parsed, "condition", None)
    grade_company = grade_company or getattr(parsed, "grade_company", None)
    grade = grade or getattr(parsed, "grade", None)
    raw = RawListing(source="compile", listing_id="cli", title=title, price=Decimal(str(ask)), currency=currency,
                     description=description, shipping=Decimal(str(shipping)) if shipping not in (None, "") else None,
                     condition=condition, grade_company=grade_company, grade=grade, market=market_for("ebay", currency), game=game)
    home = eng.profile["home_currency"]
    cal = eng.ledger.calibration(now)
    book = PriceBook(eng.store, eng.fx, eng.profile, now, bias=cal.resale_bias)
    op = evaluate_listing(raw, an, book, eng.catalog_for(game), eng.ledger, now)
    series = {}
    for sc in op.scenarios[:5]:
        v = book.value(sc.printing_id, raw.grade_company, raw.grade, raw.condition)
        points = [(s.when, s.price) for s in v.sales]
        sm = summarize(points, now)
        series[sc.printing_id] = {
            "label": sc.label, "weight": round(sc.weight, 4), "comps": len(v.comps),
            "same_condition": v.same_condition, "restated": v.adjusted, "n_sales": len(points),
            "median_30": sm["median_30"], "change_90": sm["change_90"]["pct"],
            "trend": sm["trend"], "plain": plain_trend(sm),
        }
    return {
        "at": now.isoformat(),
        "input": {"title": title, "ask": str(ask), "currency": currency.upper(), "condition": raw.condition,
                  "grade_company": raw.grade_company, "grade": raw.grade, "game": game, "home_currency": home},
        "stages": ["parse", "identity", "price", "trend", "verdict"],
        "parsed": _parsed(an),
        "identity": {"status": an.identity.status.value, "printing": getattr(an.identity.printing, "id", None),
                     "candidates": len(an.identity.candidates), "evidence": an.identity.evidence,
                     "blocking": an.identity.blocking_reasons, "evidence_needed": an.identity.evidence_needed},
        "verdict": asdict(op),
        "series": series,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.compile")
    ap.add_argument("--title", required=True)
    ap.add_argument("--ask", required=True)
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--description", default="")
    ap.add_argument("--shipping")
    ap.add_argument("--condition")
    ap.add_argument("--grade-company")
    ap.add_argument("--grade")
    ap.add_argument("--game", default="ygo", choices=["ygo", "pkm"])
    ap.add_argument("--store")
    a = ap.parse_args(argv)
    out = run(a.title, a.ask, a.currency, description=a.description, shipping=a.shipping, condition=a.condition,
              grade_company=a.grade_company, grade=a.grade, game=a.game, store=a.store)
    print(json.dumps(out, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
