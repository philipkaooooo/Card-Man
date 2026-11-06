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
Local pages over the store on 127.0.0.1, standard library only. Amounts show in USD by default
(switch to TWD in the header); conversion uses the listing's own FX at valuation time, and prices you
may pay round down while resale thresholds round up, so the display never flatters a deal.
- **The call** (`cardman/advice.py`): Buy / Buy after checks / Offer lower / Skip / Hold off, with the price
  to pay and the reasons in plain sentences, what to do before buying, and a confidence level (sales count,
  age of the newest sale, price spread, source variety, synthetic data).
- **The numbers**: ask and all-in cost, pay up to (hits the target), walk away above (loses money at the
  low-end resale), profit at the ask, break-even resale and how far below the cheapest sale it is.
- **Charts**: buy-price zones with the ask marked; profit at the ask against each recent sale with the target
  line (profit in X of N, target in Y of N); sales over time; where a low-end resale goes; what moves the
  pay-up-to price and whether the ask still fits.
- **What could go wrong**: flags (thin cushion, low confidence, stale or spread-out prices, selling slower
  than the holding assumption, no returns reserve, FX spread not set for foreign-currency shipping via
  `shipping_fx_spread_pct` in the listing's acquisition costs) and the result at 10 / 20 / 30% under the low end.
- **The math**: the pay-up-to price worked out step by step, and every assumption used.
- **Deals** ranks listings by call, then room under pay-up-to; **Evidence** shows coverage per printing and
  why each record is or is not used; **Import** uploads a manual CSV.

### Scan a listing (`cardman/listing_text.py`, `cardman/scan.py`, page **Scan a listing**)
Paste a title and description as the seller wrote them (English or Japanese) with the ask.
- Reads the seller's claims: set code, rarity (EN names and JP slang such as レリーフ, クオシク, ウルパラ, シク),
  edition, grade (PSA / BGS / CGC / SGC / ARS), condition (NM...DMG, 美品, 傷あり), lots and quantities,
  binders and collections, and risk wording (proxy / orica / custom, Asian-English, Korean, stock photo,
  no returns, damage, reprint, slab only, mystery packs). These stay seller claims; they never resolve identity.
- Lists every printing the listing could be, each priced from the store's clean sales (same rules as the deal
  page), with pay-up-to and profit at the ask.
- Finds what the seller may have missed: a code that was also printed in a pricier rarity, a rarity the code
  was never printed in, a title with no code across prints of very different value, an ask far under every
  sale (misdescribed, damaged or fake), a price gap between marketplaces, an old newest sale.
- Verdict: worth a look (profitable even as the cheapest plausible print), only if it's the right print
  (with the photo that settles it and how much money that photo decides), pass, need data, or skip (not a
  genuine in-scope single). A priority number (expected profit plus a quarter of the swing) orders the queue.

### Results (`cardman/outcomes.py`, `cardman/backtest.py`, page **Results**)
- Outcome ledger: offers, counters, purchase, extra costs, grade, listed, sold (fees, shipping), returned,
  written off, passed, notes and mistakes. Append-only in the store; amounts converted with stored FX.
- Each closed position is compared with the snapshot it was bought on: predicted vs realized profit, whether
  the low-end resale was actually reached, days to sell vs the holding period priced in. Lessons are shown
  with their evidence (estimate too optimistic, selling slower than assumed, positions past plan, losses).
- CardMan vs a price tracker: the same listings decided by the engine and by a "20% under the 90-day median of
  everything sold-looking" rule, scored on the real resale when recorded or on later clean sales of that exact
  print otherwise: buys, profit, return on capital, money-losing buys, missed winners, unknown outcomes.
- FX rates used by any run are now kept in the store, so old sales can be converted later (inverse pairs work).

### Run
```
pip install pytest && python -m pytest -q                      # 127 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
python -m cardman.store --store data/store/evidence.sqlite import my_sales.csv   # accumulate comps
python -m cardman.slice listing.json --store data/store/evidence.sqlite          # value from the store
python -m cardman.ui --store data/store/evidence.sqlite                          # http://127.0.0.1:8765
# the Scan page needs the identity cache: python -m cardman.identity_cache sync
```

### Remaining gaps
1. Network policy: open the hosts above, then test YGOResources (edition-level data) and YGOPRODeck live.
2. EN edition (1st / Unlimited / Limited) and release dates: not in YAML Yugi; EN prints abstain until a
   source with editions is reachable. Asian-English prints: no source yet (codes are quarantined on sight).
3. eBay app keys for Browse; account exports for Product Research; no sold-data source is reachable yet,
   so there are no real market observations.
4. FX rates are still per listing file; comps from older runs need a rate near their sale date in that file.
5. Image/OCR, grading sensitivities, Taiwan/JP sources, portfolio outcomes: not started.
