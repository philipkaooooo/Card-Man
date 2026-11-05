"""Live eBay Browse pull for the regression cards (asking prices and bids only, never sold comps).

Run:  python -m cardman.ebay_live [--db data/cache/market.sqlite] [--raw data/raw] [--max-pages 1]

Steps:
1. OAuth client-credentials token from EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (never printed or stored).
2. Browse searches for each regression query; raw pages go to the raw store before parsing.
3. Each item becomes an ACTIVE_ASKING or OBSERVED_BID observation in the evidence store.
4. The ebay_browse row of data/source_registry.json records the outcome (success or the exact failure).

Uses only eBay's documented, keyed Browse API with the adapter's rate limit. No scraping.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .ebay_browse import EbayBrowse, NotConfigured, SourceUnavailable
from .evidence import RawEvidenceStore
from .identity import Catalog
from .identity_sources import normalize_yaml_yugi
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "source_registry.json"
YGO_CCG_CATEGORY = "183454"  # eBay US: Yu-Gi-Oh! TCG Individual Cards

REGRESSION_QUERIES = [
    "Blue-Eyes White Dragon LOB-EN001",
    "Blue-Eyes White Dragon LOB-001",
    "Ash Blossom & Joyous Spring",
]


def regression_catalog() -> Catalog | None:
    data = ROOT / "tests" / "data" / "yaml_yugi"
    prov = data / "PROVENANCE.json"
    if not prov.exists():
        return None
    rev = json.loads(prov.read_text(encoding="utf-8"))["revision"]
    fams, prints = {}, []
    for f in sorted(data.glob("[0-9]*.json")):
        n = normalize_yaml_yugi(json.loads(f.read_text(encoding="utf-8")),
                                f"yaml_yugi@{rev[:12]}:data/cards/{f.name}", file_key=f.stem)
        fams.setdefault(n.family.id, n.family)
        prints += n.printings
    return Catalog(list(fams.values()), prints)


def update_registry(path: Path, *, ok: bool, now: datetime, reason: str | None, detail: str,
                    authenticated: bool = False) -> None:
    rows = json.loads(path.read_text())
    for r in rows:
        if r["source"] == "ebay_browse":
            r["tested"], r["tested_at"] = True, now.isoformat()
            r["credential_present"] = bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))
            r["authenticated"] = ok or authenticated
            r["available"] = ok
            r["permitted"] = "yes: keyed Browse API under eBay API License Agreement" if ok else r.get("permitted", "unknown")
            if ok:
                r["last_success"] = now.isoformat()
            r["failure_reason"] = reason
            r["probe_detail"] = detail
    path.write_text(json.dumps(rows, indent=1))


def run(db: Path, raw: Path, max_pages: int, queries: list[str], registry: Path = REGISTRY,
        browse: EbayBrowse | None = None) -> int:
    now = datetime.now(timezone.utc)
    raw_store = RawEvidenceStore(raw)
    browse = browse or EbayBrowse(raw_store)
    try:
        browse.token()
    except NotConfigured as e:
        update_registry(registry, ok=False, now=now, reason=str(e), detail="no credentials in environment")
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    except SourceUnavailable as e:
        update_registry(registry, ok=False, now=now, reason=f"OAuth token request rejected: {e}",
                        detail="token endpoint refused the keys")
        print(f"FAIL: OAuth token request rejected ({e}). Check the keys are Production, not Sandbox.",
              file=sys.stderr)
        return 3
    print("OAuth token: OK")
    catalog = regression_catalog()
    store = Store(db)
    totals: Counter = Counter()
    for q in queries:
        try:
            res = browse.search(q, catalog=catalog, category_ids=YGO_CCG_CATEGORY, max_pages=max_pages, now=now)
        except SourceUnavailable as e:
            update_registry(registry, ok=False, now=now, reason=f"search failed: {e}", detail=f"q={q!r}",
                            authenticated=True)
            print(f"FAIL: search {q!r}: {e}", file=sys.stderr)
            return 4
        new = sum(store.add_observation(o, now=now) for o in res.observations)
        kinds = Counter(o.evidence_type.value for o in res.observations)
        totals.update(kinds)
        prices = sorted(float(o.original_amount) for o in res.observations if o.currency == "USD")
        mid = prices[len(prices) // 2] if prices else None
        print(f"{q!r}: total={res.total} fetched={len(res.observations)} new={new} {dict(kinds)} "
              f"USD median ask/bid={mid}")
    update_registry(registry, ok=True, now=now, reason=None,
                    detail=f"live pull {dict(totals)} across {len(queries)} queries")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "cache" / "market.sqlite")
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--query", action="append")
    a = ap.parse_args()
    a.db.parent.mkdir(parents=True, exist_ok=True)
    return run(a.db, a.raw, a.max_pages, a.query or REGRESSION_QUERIES)


if __name__ == "__main__":
    sys.exit(main())
