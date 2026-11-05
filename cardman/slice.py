"""End-to-end vertical slice: listing -> identity -> comps -> scenarios -> ceiling -> stage.

usage: python -m cardman.slice listing.json observations.csv [--now ISO]
The catalog comes from the identity cache ("catalog_db": path to identity.sqlite) or, for
code tests, from a Python module ("catalog_module"). Synthetic observations are refused unless --allow-synthetic.
"""
from __future__ import annotations

import argparse
import importlib
import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from .economics import AcquisitionCosts, CeilingInputs, ExitCosts, buy_ceiling, scenarios_from_comps, sensitivities
from .evidence import import_manual_csv, select_comps
from .fx import FxRate, FxTable
from .identity import ListingSignals, resolve
from .workflow import evaluate


def run(listing: dict, obs_csv: Path, now: datetime, allow_synthetic: bool = False) -> dict:
    if listing.get("catalog_db"):
        from .identity_cache import IdentityCache
        cache = IdentityCache(listing["catalog_db"])
        if not cache.revision():
            raise SystemExit(f"identity cache {listing['catalog_db']} is empty: run python -m cardman.identity_cache sync")
        catalog = cache.catalog()
    else:
        catalog = importlib.import_module(listing["catalog_module"]).CATALOG
    sig = ListingSignals(**listing["signals"])
    ident = resolve(sig, catalog)
    home = listing.get("home_currency", "TWD")
    fx = FxTable([FxRate(r["base"], r["quote"], D(r["mid"]), datetime.fromisoformat(r["as_of"]), r["source"],
                         D(r.get("spread_pct", "0"))) for r in listing.get("fx", [])])
    observations = import_manual_csv(obs_csv, now)
    if allow_synthetic:
        observations = [o.__class__(**{**o.__dict__, "synthetic_fixture": True}) for o in observations]
    report = {"identity": {"status": ident.status.value, "candidates": [p.id for p in ident.candidates],
                           "evidence": ident.evidence, "blocking": ident.blocking_reasons,
                           "evidence_needed": ident.evidence_needed}}
    comps, rejected, prices = [], [], []
    if ident.printing:
        comps, rejected = select_comps(observations, ident.printing.id, as_of=now, include_synthetic=allow_synthetic,
                                       grade_company=listing.get("grade_company"), grade=listing.get("grade"))
        for c in comps:
            prices.append(fx.convert(c.original_amount, c.currency, home, c.event_time))
    report["comps"] = [{"id": c.id, "date": c.event_time.isoformat(), "source": c.source, "type": c.evidence_type.value,
                        "amount": str(c.original_amount), "currency": c.currency, "home": str(p), "url": c.source_url,
                        "synthetic": c.synthetic_fixture} for c, p in zip(comps, prices)]
    report["rejected_evidence"] = rejected
    sc = scenarios_from_comps(prices)
    report["exit_scenarios"] = {k: (str(v) if v is not None else None) for k, v in sc.__dict__.items()}
    ceiling, sens = None, None
    if sc.conservative is not None:
        cost = listing["costs"]
        inp = CeilingInputs(sc.conservative, AcquisitionCosts(**{k: D(v) for k, v in cost["acquisition"].items()}),
                            ExitCosts(**{k: D(v) for k, v in cost["exit"].items()}), D(cost["target_return"]),
                            D(cost.get("holding_months", "0")), D(cost.get("capital_cost_monthly", "0")))
        res = buy_ceiling(inp)
        ceiling, sens = res.buy_ceiling, sensitivities(inp)
        report["buy_ceiling"] = {"ceiling": str(ceiling) if ceiling else None,
                                 **{k: str(v) for k, v in res.breakdown.items()}, "calc_version": res.calc_version}
        report["sensitivities"] = {k: (str(v) if v is not None else None) for k, v in sens.items()}
    else:
        report["buy_ceiling"] = {"ceiling": None, "reason": sc.abstain_reason or "identity unresolved"}
    ask = listing.get("ask")
    ask_home = fx.convert(D(ask["amount"]), ask["currency"], home, now) if ask else None
    ev = evaluate(listing_ref=listing["listing_ref"], identity=ident, n_comps=len(comps), min_comps=3,
                  ask_all_in=None, ceiling=ceiling, ask_price=ask_home,
                  listing_last_seen=datetime.fromisoformat(listing["last_seen"]) if listing.get("last_seen") else None,
                  now=now, authenticity_ok=listing.get("authenticity_ok"), snapshot_payload=report)
    report["status"] = {"stage": ev.stage.value, "snapshot_id": ev.snapshot_id, "blocked_by": ev.reasons,
                        "expires_at": ev.expires_at.isoformat(), "human_action": "pending YES/WAIT/NO"}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("listing"); ap.add_argument("observations")
    ap.add_argument("--now"); ap.add_argument("--allow-synthetic", action="store_true")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    print(json.dumps(run(json.loads(Path(a.listing).read_text()), Path(a.observations), now, a.allow_synthetic),
                     indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
