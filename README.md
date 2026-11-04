# CardMan: private Yu-Gi-Oh! buying intelligence

Scope and rules: see the project's CLAUDE.md execution contract (JP OCG + EN TCG, TWD default, no Pokémon, no SaaS).

## Status (2026-11-04, second pass)

### Data access (tested, see `data/source_registry.json`)
- **Working:** YAML Yugi (github.com/DawnbrandBots/yaml-yugi, compiled from Yugipedia) through the git proxy.
  Revision `f0bbcfe1` (2026-11-04): 14,416 cards, 86,185 JP/EN printings in the local cache.
- **Blocked (HTTP 403 from the environment's network proxy):** YGOResources, YGOPRODeck, Konami DB, eBay,
  TCGplayer, PriceCharting, PSA, Yahoo! Auctions JP, Mercari JP. The network setting has to allow these hosts.
- **No credentials set:** eBay, TCGplayer, PriceCharting.

### Identity
- `identity_sources.py`: YAML Yugi normalizer (family = Konami ID; artwork = passcode, so alternate-art
  passcodes stay separate; printing = region + set code + product + rarity). Placeholder codes like
  `MAMS-EN???` and entries without a rarity are `incomplete` and never resolve. YGOPRODeck / YGOResources
  adapters exist but have not seen a live response.
- `identity_cache.py`: SQLite cache. Sync is incremental by git blob hash, reads the committed bytes of a
  given revision, retires (never deletes) printings that vanish, keeps a per-printing history and a sync log,
  and leaves the previous revision intact if a sync fails. Real run: revision `6a82bdb8` -> `f0bbcfe1`
  re-read 285 files, removed 80, +341 / ~6 / -170 printings in 0.5 s (full sync: 9 s).
- Resolver: codes without a language segment (`LB-01`, `SKE-001`, `JMP-001`) take region from the catalog;
  `verified_product` narrows code-less vintage prints; longest-name matching ("Dark Magician" is not
  "Dark Magician Girl"); English prints never resolve while the loaded source has no edition data.
- `tests/test_identity_real.py`: 18 traps on real printings (pinned subset in `tests/data/yaml_yugi`).

### Market
- `ebay_browse.py`: OAuth client credentials, paginated search, backoff on 429/5xx, raw pages stored first,
  outage raises instead of returning "no listings". Maps to ACTIVE_ASKING / OBSERVED_BID only; Best Offer
  flagged. Untested live (no keys, host blocked).
- Manual import (`examples/manual_import_TEMPLATE.csv`) is the path for sold, accepted-offer (eBay Product
  Research) and Japanese marketplace records until an API path opens.

### Run
```
pip install pytest && python -m pytest -q                      # 66 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
```

### Remaining gaps
1. Network policy: open the hosts above, then test YGOResources (edition-level data) and YGOPRODeck live.
2. EN edition (1st / Unlimited / Limited) and release dates: not in YAML Yugi; EN prints abstain until a
   source with editions is reachable. Asian-English prints: no source yet (codes are quarantined on sight).
3. eBay app keys for Browse; account exports for Product Research; no sold-data source is reachable yet,
   so there are no real market observations.
4. Persistent evidence/decision store (identity cache is SQLite; evidence and decisions are still in memory).
5. Image/OCR, grading sensitivities, Taiwan/JP sources, portfolio outcomes: not started.
