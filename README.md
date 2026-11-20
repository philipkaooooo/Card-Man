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

### Continuous engine (`engine.py`, page **Opportunities**)
- `python -m cardman.engine run` (or the button on the Opportunities page) reads new files dropped in `data/inbox/`
  (CSV, JSONL, saved pages with schema.org data) and eBay once keys exist, keeps every listing's price history, and
  re-analyzes only what changed. Each listing becomes an opportunity with the printings it could be, weights, a
  value of information ("a check is worth"), the next research step, and a stage: Observe, Spectate, or escalated to a
  stored valuation (the deal page) once a person confirms the card's code / edition / authenticity.
- Market signals: price shifts, volume spikes, regional gaps that survive costs, sellers listing a lot at once.
- Pricing (`pricing.py`): recency- and trust-weighted quantiles, a robust trend that can lower (never raise) the
  estimate for the expected time to sell, liquidity, chance of loss and a max buy at a set confidence.
- Market read (`read.py`): this mark is the resale the pay-up-to price and the engine's max-buy use.
  Eight or more sales in 14 days set it at their median, even if the month before was higher. Fewer fresh
  sales, and falling, set it at the low of the last three. One or two fresh sales under 85% of the prior
  month set it at the lowest of those. A live ask under that replaces it. The listing being bought is not
  that ask. An index more than 12% above recent sales is discarded. A source with one sale cannot veto a
  source with three or more. If the sources that do have a run disagree, the spread is not one price, or
  nothing sold in 45 days, there is no pay-up-to: the last-10 window is not used instead. A small settled
  sample with no such flag still uses its own low sale. The prior month stays in the history. A cross-posted
  sale has to match within 0.1% and one day. Listing counts add only this printing.
- The keyword scanner (`market_live.py`, `spread.py`) does not run. A search mixes printings and a scraped
  tile is not a sale. One product's SKUs are `python -m cardman.tcgplayer`.
- English edition rules (`data/edition_rules.json`, hand-written, to be checked against a source) split edition-less
  English prints into edition scenarios priced from confirmed-edition sales.
- Costs come from `data/profile.json`; until it exists the engine uses `examples/cost_profiles_ASSUMED.json`.
- The Results page and the engine share one outcome table: a purchase recorded in either place counts in both, and
  the engine's resale calibration learns from it.

### Run
```
pip install pytest && python -m pytest -q                      # 220 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
python -m cardman.store --store data/store/evidence.sqlite import my_sales.csv   # accumulate comps
python -m cardman.slice listing.json --store data/store/evidence.sqlite          # value from the store
python -m cardman.ui --store data/store/evidence.sqlite                          # http://127.0.0.1:8765
python -m cardman.tcgplayer yugioh "LOB-001" --html lob.html                  # same-SKU asks, the trend, and a chart of the bucket lows
# the Scan page needs the identity cache: python -m cardman.identity_cache sync
```

### Remaining gaps
1. Editions. YAML, YGOResources and YGOPRODeck still have no 1st / Unlimited / Limited field. YGOResources
   prints are `{code, date}` only (live revision 33276). Yugipedia galleries are the check, and they are not a
   stamp photo. Worldwide English is Unlimited-only, NA is 1st and Unlimited, for LOB, MRD, Spell Ruler, PSV,
   LON, LOD, DCR and IOC. `editions_for` drops 1st from a code whose suffix starts with EN
   (`LOB-EN001`). The NA code with no language segment (`LOB-001`) keeps both. A verified 1st
   on `LOB-EN001` is a CONFLICT, not a price. Magic Ruler redirects to Spell Ruler; the
   1st-edition NA gallery is titled Magic Ruler. `code_key` still collapses `LOB-EN001` onto
   `LOB-001` so a date can be found. `is_original` True means that key holds the earliest
   listed date, not that the card is a 1st Edition.
2. Asian-English. YGOResources does have AE code and date. On the vintage samples those codes collide with
   English: `LOB-001`, `SDK-001`, `LOB-005`, `SDY-006`, `MFC-000`. Quarantine stays. Do not merge them into
   the EN namespace.
3. Japanese prints on YGOResources are not the catalog. Konami ids 4007, 4041 and 4766 each returned one
   2026 anniversary row; the pinned YAML has 58, 55 and 37. No normalizer until a ja payload is complete.
   yaml-yugi HEAD `d3b999759760` vs the pin: English rows on the six pinned cards did not change, and no
   edition field appeared. Three new JA ultras did: `MVB1-JP010` Blue-Eyes, `MVB1-JP027` Dark Magician,
   `MVB1-JP028` Dark Magician Girl, all the Dark Side of Dimensions 10th Anniversary Movie Box.
4. Solds. Browse, when keyed, is asks and bids. Product Research has no developer endpoint. Nothing here
   added a settled comp. TCGplayer's public product page is readable without a partner key
   (`cardman/tcgplayer.py`): a product is one card in one set, a SKU is condition × variant × language.
   `marketPrice` is not a sale and, on LOB-001 product 21792, one number covers 1st Edition and Unlimited.
   `screen` buys the cheapest ask of one SKU and exits at the next ask of that same SKU after a
   Level 1-4 fee (10.75% capped at $75, plus 2.5% and $0.30). A gap that dies is NO. A gap that
   survives is LOOK, and it is still not a return. The per-listing sales table is signed and a
   naked `latestsales` path stays closed. The chart is not: `price/history/{id}/detailed` returns
   cleared low and high per SKU when called the way the product page calls it. `judge` buys an ask
   only against that same variant, condition and language. A low under 20% of its own bucket's
   market price is thrown out. Fewer than three usable sales is THIN, not a buy. The exit is the
   transaction-weighted median of the bucket lows, so one high day cannot set it. On the 2026-11-21
   pull, Unlimited Damaged cleared ten times, five of them at 19, and the 26.60 day is not the exit:
   the 19.87 ask is NO after fees. 1st Edition Near Mint cleared twice in a year, at 14.99 and 4000,
   so it is not a book. Unlimited Near Mint's median low was 119.99 and the 161.99 ask is NO.
   Market minus lowest on 21792 is a misread. The cheap ask is Damaged Unlimited.
   LOB-EN001 25th (486054) and Worldwide English (476160) are different products. Pokémon is line 3 and
   Pokémon Japan is line 85; neither is fed to the Yu-Gi-Oh resolver. PriceCharting and YGOPRODeck
   `card_prices` / `set_price` are not sales either.
5. FX. A mid print, not wired into the store: USD/TWD 32.986624 and USD/JPY 159.74951 at 2026-11-20 00:02:32
   UTC (open.er-api). Frankfurter JPY 159.97 on 2026-11-19, and no TWD. `fx.py` rejects a rate older than
   one day for the timestamp being converted. Old comps still need a rate near their own sale date.
6. Costs. `data/profile.json` is still absent. The assumed file uses the published eBay.com CCG rate,
   13.25%, plus the $0.40 per-order fee as 13.19 TWD at the print above. Scan's US-unit costs use $0.40
   directly. 13.25% of buyer-paid shipping is still not in either model. These are not a measured Taipei payout.
7. Still closed: PSA cert (403), Yahoo Auctions closed results (500), OCR (needs images and `swiftc`).
