# CardMan: private card-market buying intelligence (Yu-Gi-Oh! OCG/TCG + Pokémon)

Pricing rules: `docs/pricing-method.md` — a price comes from real sales of one exact printing, in one exact condition.

## Status (2026-11-21)

### Data access (tested, see `data/source_registry.json`)
- **Working:** YAML Yugi (github.com/DawnbrandBots/yaml-yugi, compiled from Yugipedia) through the git proxy.
  Incremental sync into `data/cache/identity.sqlite` (printings, history, retire-don't-delete).
- **Sold data:** saved result pages from eBay, 130point, Yahoo! Auctions and Mercari are read by the Import page
  (`cardman/sold_import.py`, `cardman/sold_pages.py`); manual CSV remains the fallback. A guide price is a lead,
  never a sale; each catalog version is priced from its own row (`cardman/tcgplayer.py`, `cardman/catalog/`).
- **Catalogs:** Pokémon (`data/cache/pkm.sqlite`), Yu-Gi-Oh! (`identity.sqlite`) and the joined master list
  (`catalog.sqlite`, one TCGplayer price per version) are re-downloaded automatically when missing
  (`cardman/catalog_boot.py`).
- **Blocked without keys or an open network:** eBay Browse API, live TCGplayer, PriceCharting, PSA.

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
- `pc.py`: the PriceCharting cross-market. `python -m cardman.pc sweep` launches a headed Chrome over the
  DevTools pipe (the site's Cloudflare challenge passes in the real browser, not in a scripted client),
  resolves each product through the site search once, and stores the six grade prices, the sales-volume note
  and a year of weekly points in `pc_card` (~2 KB a card). Paced at about one card every few seconds, skips
  rows younger than ten days; `--refetch` re-checks a set. The market board's `1 yr · PC` column reads it
  against our sold medians; a card whose PriceCharting raw price sits more than 3x from ours is treated as a
  different printing. The grading strip buys raw at the cheapest live ask (linking the listing), adds inbound
  shipping and the PSA fee from `data/profile.json`, and nets the PSA 10 after selling fees: it names the
  profit in currency and the most the raw may cost when no ask is low enough. Each card page carries a
  PriceCharting section of its own (`ui_pc.py`): all six grades selectable, one line each, hover for the
  week's sold price, plus the table and PSA/CGC population.
- `alerts.py` catches asks under the typical sold price the moment they land: the engine scans each batch
  right after it is analyzed and sweeps the whole board at the end of every pass, writing one `ASK_UNDER`
  signal per printing per price point (fee math identical to the under-typical section: raw in, raw out).
  The bell in the header polls `/alerts.json` every 40 s, badges what is unseen, toasts new asks while the app
  is open, and can post a desktop notification after one explicit click. Every alert links to its card page.
- Alerts are followed to their end (`alert_outcomes.py`): the listing ended sold / accepted an offer is direct
  evidence, a vanished ask counts as sold near the ask only when a raw sale of the same print lands inside the
  ask band within 21 days, and anything else is gone. The Alerts page (`/alerts`) shows sell-through, the money
  caught and the edge left on the table per week, plus every followed signal with what actually happened; the
  bell shows each ask's state and how past alerts on that print resolved. Sell-through feeds back: with four or
  more resolved alerts, a print under 25% stops alerting, and the bell and toasts are ordered by money at stake
  times that print's past sell-through, each ask carrying a meter of its rate.
- The board's **Trend** column (`trends.py`) uses one rule on every row: the median of the newest k sales
  against the median of the k before them, k = min(5, sales/2). Up 5%+ is Climbing, down 5%+ Slipping,
  inside that Holding. Too few sales for that, PriceCharting's own weekly series for the same grade supplies
  the call from its 12-month change (the cell says `PC 12 mo`); only rows where neither can answer are Thin.
  The header draws the same four categories as a segmented bar with a clickable count tile each (a second
  click clears the filter), plus how many live asks sit under typical and the listed / sold-30d totals.
- A card page carries the same call beside the price (`_trend_box` in `ui_home.py`): direction, percent, and
  the evidence in plain words, with PriceCharting's weekly line drawn small and linked to the grade table.
  The old price-over-time chart is gone - it drew one flat line from a handful of backfilled sales, so the
  space belongs to the trend and the sales table stays below.
- Raw sales are restated to Near Mint on the condition ladder (`conditions.py`) before any median, and a raw
  ask in a played condition is restated up to NM the same way, so a cheaper lower-grade copy never poses as a
  cheap NM copy. Unknown condition stays NM - it is never guessed.

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
- English edition rules (`data/edition_rules.json`, hand-written, to be checked against a source) split edition-less
  English prints into edition scenarios priced from confirmed-edition sales.
- Costs come from `data/profile.json`; until it exists the engine uses `examples/cost_profiles_ASSUMED.json`.
- The Results page and the engine share one outcome table: a purchase recorded in either place counts in both, and
  the engine's resale calibration learns from it.

### Run
```
pip install pytest && python -m pytest -q                      # 506 pass
python -m cardman.sources                                       # re-probe every source
python -m cardman.identity_cache sync                           # clone/update YAML Yugi, sync cache
python -m cardman.slice examples/identity_only_LB-01.json examples/manual_import_TEMPLATE.csv
python -m cardman.store --store data/store/evidence.sqlite import my_sales.csv   # accumulate comps
python -m cardman.slice listing.json --store data/store/evidence.sqlite          # value from the store
python -m cardman.ui --store data/store/evidence.sqlite                          # http://127.0.0.1:8765
# the Scan page needs the identity cache: python -m cardman.identity_cache sync
```

### Remaining gaps (2026-11-21)
1. eBay app keys for Browse; sold data arrives as saved pages until then, so freshness depends on you re-saving.
2. Japanese print dates: YGOResources lists English prints broadly and JP ones poorly — a missing date is unknown,
   never "not the original".
3. Two pricing lines ran in parallel on `main` and this branch (`mark`/refused-book caps over there, simulated
   max-buy and an engine-checked spread scanner here). The one-SKU TCGplayer screening from `main` is imported;
   the caps line is not merged. Pick one economics before further `main` work.
4. The real-catalog registry tests skip until `catalog.sqlite` has been built once with an open network
   (`python -c "from cardman.catalog_boot import ensure; ensure()"`).
