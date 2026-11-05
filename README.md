# CardMan: private Yu-Gi-Oh! buying intelligence

Scope and rules: see the project's CLAUDE.md execution contract (JP OCG + EN TCG, TWD default, no Pokémon, no SaaS).

## Status (2026-11-05)

### Data access (tested, see `data/source_registry.json`)
- **Working, with adapters:** YAML Yugi (git), YGOResources, YGOPRODeck, Konami DB (JP page per card).
- **Reachable, need credentials:** eBay Browse (EBAY_CLIENT_ID/SECRET), TCGplayer, PriceCharting, PSA.
- **Reachable, no adapter yet:** Yahoo! Auctions JP, Mercari JP (terms review needed before any capture).
- No source credentials are set.

### Live cross-check (`python -m cardman.live_identity 4007 4041 4766 8007 12950`)
- Konami DB confirms every coded JP printing YAML Yugi lists for these five cards, gives release dates, and
  adds prints YAML Yugi lacks (BEWD KA-04/KA-05 in Structure Deck: Kaiba; Oct 2026 YAC1/YAS3 prints).
- YGOResources dates each print **code**, not each product (LOB-EN001 carries 2023-04-21 although it is also
  listed under the original LOB product), so a date is attached only when one product uses the code. It lists
  Asian-English codes: LOB-001 and SDK-001 are both EN TCG and Asian-English codes. Its JP list is sparse.
- YGOPRODeck omits some products (e.g. Legendary Collection Gameboard Edition); disagreements are stored.
- None of these sources lists editions. An English print resolves only with a verified edition-stamp photo,
  and the edition becomes part of the printing id (1st and Unlimited comps never pool).
- Corrected fixture: BEWD's first JP release is STARTER BOX (1999-03-18, Konami), not Vol.1.

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

### Evidence and decisions (`store.py`)
- Observations are written once; rewriting one with different content is refused. Cancellations, refunds,
  relists and invalid findings are separate status events, read back as of the decision time.
- Each valuation is a snapshot keyed by a hash of its inputs and outputs; `replay` recomputes it and
  detects tampering. Alerts are unique per listing and snapshot and survive restarts.
- Invalidating a printing (or the identity cache retiring it) invalidates every snapshot that used it,
  blocks its alerts and refuses YES on it.

### Run
```
pip install pytest && python -m pytest -q                      # 87 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
```

### Remaining gaps
1. Cross-check runs per card on demand; run it for each card before it can reach Qualified.
2. EN edition (1st / Unlimited / Limited) comes only from stamp photos: no reachable source lists editions.
3. eBay app keys for Browse; account exports for Product Research; Yahoo! Auctions JP / Mercari need a terms
   decision. There are still no real market observations.
4. Evidence/decision store exists (`cardman/store.py`, `--store` on the slice); comp retrieval still reads the
   CSV of a run rather than querying the store across runs.
5. Image/OCR, grading sensitivities, Taiwan/JP sources, portfolio outcomes: not started.
