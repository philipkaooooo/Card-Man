# How CardMan prices a card

One rule runs through everything: **a price comes from real sales of one exact printing, in one exact condition or
grade.** Nothing is ever averaged across printings, editions, grades or currencies. Every step below has a test in
`tests/test_pricing_invariants.py` (plus older tests in `test_math_invariants.py`, `test_verify.py` and `test_engine.py`).

The path is short and runs in order: **sales in → duplicates out → exact match → trust → outliers → currency →
recency-weighted median → (falling) trend → your own results → max buy → verdict.**

## 1. What counts as a sale

| Evidence | Used for price? | Base trust | Why |
|---|---|---|---|
| Completed sale you entered or imported with proof (`INDEPENDENT_TRANSACTION`) | yes | 1.0 | settlement is known |
| Accepted offer / auction close (`ACCEPTED_OFFER`, e.g. 130point) | yes | 0.9 | real price, settlement not seen |
| Marketplace "sold" (`MARKETPLACE_MARKED_SOLD`: eBay sold count, saved sold pages) | yes | 0.8 × 0.85 = 0.68 | a sold badge does not prove the buyer paid (`UNPROVEN_SETTLEMENT`) |
| Best Offer accepted, price hidden (`SELLER_CLAIM`) | **no** | n/a | the shown price is the ask, not what was paid |
| Live asks, bids | **no** | n/a | used only to count competing copies (§9) |
| Guide prices (YGOPRODeck, TCGplayer market) | **no** | n/a | a *lead* ("REFERENCE_GAP"), never a value or a profit |
| Synthetic / demo records | **no** | n/a | excluded outside tests |

## 2. Duplicates: one sale is counted once

A single sale often reaches us more than once: as an eBay sold-count unit, again on a saved eBay sold page, and again
on 130point. There are two layers:

1. **Lineage** (`evidence.dedup`). Records that share an upstream id (`ebay:{item}`, or `ebay:{item}:{unit}` for a sold
   count) are one sale; the strongest evidence type is kept. Each unit of a multi-copy listing is its own sale.
2. **Cross-source match** (`verify.assess`, `CROSS_POST_DUP`). Different sources, same currency, price within 1%,
   within 2 days, and the same seller where both name one → the weaker record is the duplicate. **The match is
   one-to-one**: one kept sale absorbs at most one record from each other source.
   *Fixed in this change*: matching used to be greedy, so two separate eBay sales at the same price that sat next to one
   130point record were both swallowed, and two sales became one.

## 3. Exact match only (`evidence.select_comps`)

A sale is used for printing P only if:

- its identity is resolved to exactly P (a sale that could be 1st or Unlimited is **not** used for either);
- it is a single (`quantity == 1`, not a lot);
- the grade company and grade are equal, so raw is never pooled with PSA, and PSA 9 never with PSA 10;
- for raw cards, the condition matches when one is asked for;
- it happened before the decision time, so nothing from the future leaks in.

Up to the 10 most recent such sales are kept (`MAX_COMPS`).

## 4. Trust penalties (multiplied into the base trust)

| Flag | Effect | Rule |
|---|---|---|
| `CROSS_POST_DUP`, `RELISTED_AFTER_SALE` (same seller relists within 14 days), `SHIPPING_GAMED` (shipping > 2 × price) | excluded | a duplicate, a sale that fell through, or not the real price |
| `OUTLIER_HIGH` / `OUTLIER_LOW` | × 0.5 | modified z-score > 3.5 (median and MAD), only with ≥ 5 sales in one currency |
| `SELLER_CHURN` | × 0.5 | one seller, 3 rising sales within 30 days: possible price painting |
| `CERT_FLIP` | × 0.7 | same slab resold within 14 days for 30%+ more: one flip, not a market move |

The outlier check uses the median and MAD, not the mean and standard deviation, so the outlier itself cannot hide
itself. Below 5 sales it is skipped: with 4 points a "spread" means nothing.

## 5. Currency, once

Each sale is converted to the home currency **once**, at the rate **on the sale's own date** (open.er-api.com for today;
daily history from the fawazahmed0 snapshots, backfilled on demand). If no rate exists within 2 days, the sale
abstains and the reason is noted; it is never silently converted at today's rate. Asks are converted at their capture
time. Currencies are never mixed before conversion.

## 6. The price itself (`pricing.estimate`)

- **Fewer than 3 sales → no price** (`MIN_SALES = 3`). One or two sales say nothing reliable about the market.
- **Weight per sale** = trust × 0.5^(age in days / 90). A sale 90 days old counts half as much as one from today.
- **Middle** = weighted *median*, not mean: one wild sale cannot move it.
- **Range**:
  - with 3–5 sales ("sparse"), low = the **lowest** sale and high = the middle. This is cautious on purpose and acts as
    small-sample shrinkage: a thin card never gets an optimistic range.
  - with 6 or more, low/high = the weighted 20th and 75th percentiles.
- **Effective sample size** = (Σw)² / Σw². This shows when a few recent sales dominate.
- **Confidence interval on the middle**: 300 weighted bootstrap resamples, seeded from the data, so the same sales
  always give the same answer.

## 7. Trend: down only

The trend is a Theil-Sen slope on log price per month, needing ≥ 5 sales spanning ≥ 30 days. It is robust to outliers.

- A **falling** market is priced where it will be when this copy sells. The horizon is the expected wait at the
  observed pace, between 1 week and 2 months.
- A **rising** market is **not** paid for: we never buy on extrapolated growth.

## 8. Your own results

Once you record real outcomes, the ratio of realized to predicted prices becomes a bias factor ≤ 1. It is shrunk
toward 1 with `SHRINK = 5`, so 5 outcomes count as much as the prior of "no adjustment". Your own data can only make
the estimates more cautious, never more optimistic.

## 9. From price to a decision (`opportunity.analyze`)

A listing is usually one of several possible printings, each with a probability (the weights sum to 1). Each
possibility is valued **on its own sales** (§1–8). The possibilities are combined only as an expectation over *which
card it is*, never as a blended price:

- **Max buy** per printing: the highest all-in price that still clears your target return after fees, shipping and the
  cost of money for the expected holding time. The holding time comes from a queue model,
  months = (competing live copies at or under our price + 1) / sales per month (observed pace, last 180 days).
- **Expected profit** is stated only when printings with sales hold ≥ 50% of the probability (`MIN_PRICED_SHARE`).
  A small priced slice never speaks for the whole listing.
- **UNDERPRICED** needs the ask ≤ max buy for printings holding ≥ 80% of the probability (`UNDERPRICED_SHARE`). Below
  that it is RIGHT_PRINT_ONLY: confirm the print first.
- **Verdict**:
  - **BUY** needs sales-basis pricing, a resolved identity, UNDERPRICED, and all-in ≤ max buy;
  - otherwise **CHECK** (worth confirming), **WATCH** or **PASS**.

## 10. On the card page

- **Listings now**: every live listing the engine thinks is this card (≥ 95% card probability), across platforms, sorted
  by all-in. Listings below 95% go in a separate **Might be this card** group and are never mixed in. Lots are only
  counted.
- **Recently sold**: real sales only, grouped by printing; a sale whose printing is open is shown apart.
- **How we priced it** (collapsed): for each printing with a price, the number of sales, the middle, the range and how it
  was taken, and each adjustment that applied (trend, your results).
- **Coverage line**: says honestly what we hold per platform (e.g. "eBay live · Yahoo! Auctions sold only").

## Removed or kept out as noise

- No mean of sales anywhere in pricing (medians and percentiles only).
- No "all printings of this card" average: a card-level number would mix 1st edition with reprints.
- Guide prices never enter a value.
- A redundant re-filter in the opportunity step was removed; the result is unchanged.
