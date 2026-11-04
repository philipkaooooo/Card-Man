"""Source capability registry with real probe results (CLAUDE.md section 2).

A capability is only `tested`/`available` after an actual request. Probe results
are written to data/source_registry.json; nothing here is assumed to work.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# (source, capability, access_mode, probe_url or None, credential env var or None, notes)
SOURCES = [
    ("yaml_yugi", "canonical_identity", "git", "git+https://github.com/DawnbrandBots/yaml-yugi", None,
     "secondary source compiled from Yugipedia: set code/product/rarity per region; no editions, release dates "
     "or Asian-English prints; revision = git commit; synced by cardman.identity_cache"),
    ("ygoresources", "canonical_identity", "public_api", "https://db.ygoresources.com/manifest/0", None,
     "use /manifest/{revision} for cache invalidation"),
    ("ygoprodeck", "canonical_identity", "public_api",
     "https://db.ygoprodeck.com/api/v7/cardsetsinfo.php?setcode=LOB-EN001", None,
     "card_prices is not per-print sold evidence; tcgplayer_data removed Sept 2026"),
    ("konami_db", "canonical_identity", "public_web",
     "https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4007&request_locale=ja", None,
     "official OCG/TCG database; respect terms"),
    ("ebay_browse", "active_listing", "oauth_api", "https://api.ebay.com/buy/browse/v1/item_summary/search?q=x",
     "EBAY_CLIENT_ID", "needs app keys + OAuth; adapter cardman.ebay_browse (asking/bid only, never sold)"),
    ("ebay_product_research", "accepted_offer", "account_ui", None, None,
     "seller-account research UI, not a developer endpoint; import via user export/screenshots"),
    ("tcgplayer", "market_aggregate", "partner_api", "https://api.tcgplayer.com/catalog/categories",
     "TCGPLAYER_PUBLIC_KEY", "new keys not routinely granted; Market Price is not a sale"),
    ("pricecharting", "market_aggregate", "paid_api", "https://www.pricecharting.com/api/products?q=blue-eyes",
     "PRICECHARTING_TOKEN", "current values only, no historical sales; 1 req/s"),
    ("psa_cert", "graded_certification", "public_web", "https://www.psacard.com/cert/1", None,
     "cert presence is not authenticity proof"),
    ("yahoo_auctions_jp", "auction_result", "public_web", "https://auctions.yahoo.co.jp/", None,
     "closed-auction pages; check terms before any capture"),
    ("mercari_jp", "marketplace_marked_sold", "public_web", "https://jp.mercari.com/", None,
     "'sold' badge is not verified settlement"),
    ("manual_import", "marketplace_marked_sold", "user_upload", None, None,
     "user-provided CSV/screenshots with source URL; always available"),
]


def probe(url: str, timeout: float = 20) -> tuple[bool, str]:
    if url.startswith("git+"):
        try:
            out = subprocess.run(["git", "ls-remote", url[4:], "HEAD"], capture_output=True, text=True,
                                 timeout=timeout, check=True).stdout.split()
            return True, f"git HEAD {out[0][:12]}" if out else "git ok"
        except Exception as e:
            return False, f"git: {type(e).__name__}: {e}"[:200]
    req = urllib.request.Request(url, headers={"User-Agent": "cardman-probe/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # proxy denial surfaces as URLError/OSError
        return False, f"{type(e).__name__}: {e}"[:200]


def run_probes(out: Path) -> list[dict]:
    now = datetime.now(timezone.utc)
    prev = {}
    if out.exists():
        prev = {(r["source"], r["capability"]): r for r in json.loads(out.read_text())}
    rows = []
    for source, cap, mode, url, cred_env, notes in SOURCES:
        has_cred = bool(cred_env and os.environ.get(cred_env))
        row = {
            "source": source, "capability": cap, "access_mode": mode, "supported": True,
            "tested": False, "authenticated": has_cred, "permitted": "unknown", "available": False,
            "tested_at": None, "last_success": prev.get((source, cap), {}).get("last_success"),
            "failure_reason": None, "credential_env": cred_env, "credential_present": has_cred,
            "notes": notes,
        }
        if source == "manual_import":
            row.update(tested=True, available=True, permitted="yes", tested_at=now.isoformat())
        elif url:
            ok, detail = probe(url)
            row.update(tested=True, tested_at=now.isoformat())
            if ok:
                row.update(available=True, last_success=now.isoformat(), failure_reason=None, probe_detail=detail)
            else:
                row["failure_reason"] = detail
        else:
            row["failure_reason"] = "no programmatic endpoint; needs account-authorized export"
        rows.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    return rows


if __name__ == "__main__":
    rows = run_probes(Path(__file__).resolve().parent.parent / "data" / "source_registry.json")
    for r in rows:
        print(f"{r['source']:24} {r['capability']:26} available={r['available']!s:5} cred={r['credential_present']!s:5} {r['failure_reason'] or ''}")
