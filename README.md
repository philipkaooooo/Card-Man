# CardMan: private Yu-Gi-Oh! buying intelligence

Scope and rules: see the project's CLAUDE.md execution contract (JP OCG + EN TCG, TWD default, no Pokémon, no SaaS).

## Status (2026-11-04)

### (a) Audit: what existed
- No existing CardMan repository. Kao's GitHub has six repos; none contains card-market code (TB-Web is a 3D gallery site).
- No source credentials in the environment (no eBay, TCGplayer, PriceCharting or PSA keys).
- Every external card source was refused by the environment's network proxy (HTTP 403 on CONNECT):
  YGOResources, YGOPRODeck, Konami DB, eBay Browse, TCGplayer, PriceCharting, PSA, Yahoo! Auctions JP, Mercari JP.
  Results are recorded by `python -m cardman.sources` in `data/source_registry.json`.

### (b) First vertical-slice code (pure Python 3.11+, stdlib only; pytest for tests)
| Module | What it does |
|---|---|
| `models.py` | family / artwork / printing / specimen, 8 separate evidence types, Decimal money, tz-aware times |
| `identity.py` | exact-print resolver with abstention; printed vs seller-typed fields kept apart; JP/EN never cross-mapped; Asian-English and Rush Duel quarantined; cert numbers not taken as set codes; names which photo would resolve an ambiguity |
| `evidence.py` | lineage dedup (one sale through 3 feeds counts once; same slab sold twice stays two), up to 10 most recent exact-print sales, lots/grade mismatches/future data rejected, manual CSV import, content-addressed raw store |
| `fx.py` | rate at event time, stale-rate refusal, explicit conversion spread |
| `economics.py` | conservative/base/upside exits from comps (abstain under 3), closed-form buy ceiling for target return on all-in capital incl. buyer premium, proxy, duty, grading, holding cost; sensitivities |
| `workflow.py` | hard gates for Qualified (identity, authenticity, comps, ceiling, freshness), expiry, one alert per immutable snapshot, WAIT requires a trigger, YES never buys |
| `sources.py` | capability registry with real probes |
| `slice.py` | end to end: listing JSON + observations CSV -> the five outputs |

Run: `pip install pytest && python -m pytest -q` (36 pass) and
`python -m cardman.slice examples/demo_listing_SYNTHETIC.json examples/demo_observations_SYNTHETIC.csv --now 2026-11-04T12:00:00+00:00 --allow-synthetic`.
The demo data is synthetic and is refused as evidence unless `--allow-synthetic` is passed.

### (c) Remaining gaps
1. Network access to identity and market sources (environment setting) — blocks everything below.
2. Identity cache from YGOResources/YGOPRODeck with revision-aware refresh; fixture printings are `verified_against_source=False` until cross-checked.
3. Regression tests on real documented prints (JP original vs reprint, real shared-code multi-rarity sets, real promos) need the live data.
4. First real market adapter (eBay Browse needs app keys; otherwise manual import of real listings/exports).
5. Persistent DB (SQLite planned) with migrations and replayable decision snapshots; currently in-memory.
6. Image/OCR pipeline, grading sensitivities, Taiwan/JP sources, portfolio outcomes: not started.
