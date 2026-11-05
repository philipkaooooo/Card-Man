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

### Evidence and decisions (`store.py`)
- Observations are written once; rewriting one with different content is refused. Cancellations, refunds,
  relists and invalid findings are separate status events, read back as of the decision time.
- Each valuation is a snapshot keyed by a hash of its inputs and outputs; `replay` recomputes it and
  detects tampering. Alerts are unique per listing and snapshot and survive restarts.
- Invalidating a printing (or the identity cache retiring it) invalidates every snapshot that used it,
  blocks its alerts and refuses YES on it.
- Comparables come from the store across runs: with `--store`, the slice records the CSV (optional) and then
  selects comps from every observation captured by the decision time, with status events known by then
  applied. Re-importing the same row in a later run is a no-op that keeps the first capture time; a row whose
  source fact changed is still refused.
- `python -m cardman.store` maintains the store between runs: `import` a manual CSV, record a `status`
  (CANCELLED, REFUNDED, INVALID, ...), list the `comps` for a printing at a time, `replay` a snapshot,
  `decide` YES / WAIT / NO on a snapshot.

### Review UI (`python -m cardman.ui`)
Local pages over the store on 127.0.0.1, standard library only; charts are server-rendered SVG, and one
small bundled script shows tooltips (values are also always in a table next to each chart).
- **Deals**: latest valuation per listing, qualified first then by room under the ceiling: the five checks
  as icons, ask and ceiling in TWD, a room-under-ceiling bar, return at the ask, sales count.
- **Deal**: a one-line verdict (ask vs ceiling and the return at the ask against the target), the five checks
  with plain-language details and what to do next for each failing one, then:
  - *Sales, ask and ceiling*: every record of the printing over time in TWD (used comps filled, unused
    hollow, cancelled crossed) against the ask, buy ceiling and exit scenarios;
  - *Where a resale goes*: the conservative exit split into item price, buying costs, target return and
    selling costs, with the ask marked on the same scale;
  - *Profit if you buy at the ask*: profit per exit scenario against the target-return line;
  - *What moves the ceiling*: each sensitivity with the resulting ceiling and whether the ask still fits;
  - decision form (YES refused on invalidated identity), the evidence table behind the chart, identity,
    earlier valuations of the listing, calculation details.
- **Evidence**: coverage by printing (usable sales vs the 3 needed); per printing, why each record is or
  is not used, records over time per currency, and every record. Each record page shows the untouched source
  fields, status history and a form to record a new status.
- **Import**: upload a manual CSV; already-stored rows are skipped, changed rows are refused and listed.

### Run
```
pip install pytest && python -m pytest -q                      # 96 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
python -m cardman.store --store data/store/evidence.sqlite import my_sales.csv   # accumulate comps
python -m cardman.slice listing.json --store data/store/evidence.sqlite          # value from the store
python -m cardman.ui --store data/store/evidence.sqlite                          # http://127.0.0.1:8765
```

### Remaining gaps
1. Network policy: open the hosts above, then test YGOResources (edition-level data) and YGOPRODeck live.
2. EN edition (1st / Unlimited / Limited) and release dates: not in YAML Yugi; EN prints abstain until a
   source with editions is reachable. Asian-English prints: no source yet (codes are quarantined on sight).
3. eBay app keys for Browse; account exports for Product Research; no sold-data source is reachable yet,
   so there are no real market observations.
4. FX rates are still per listing file; comps from older runs need a rate near their sale date in that file.
5. Image/OCR, grading sensitivities, Taiwan/JP sources, portfolio outcomes: not started.
