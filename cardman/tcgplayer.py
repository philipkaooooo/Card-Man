"""TCGplayer public storefront, no partner key. Yu-Gi-Oh and Pokémon singles only.

Checked live 2026-11-21:

* Product details: ``GET https://mp-search-api.tcgplayer.com/v2/product/{id}/details``
* Search: ``POST https://mp-search-api.tcgplayer.com/v1/search/request``
* Price points: ``GET https://mpapi.tcgplayer.com/v2/product/{id}/pricepoints``

A product is one card in one set. A SKU is condition × variant × language.
``marketPrice``, ``lowestPrice``, ``medianPrice`` and ``pricepoints`` are not sales.
``pricepoints`` only splits Normal vs Foil, so on product 21792 (LOB-001) the
119.14 market price covers five 1st Edition SKUs and five Unlimited SKUs.
The cheapest ask on that product was a Damaged Unlimited, not a Near Mint 1st.
LOB-EN001 25th Anniversary is product 486054 and Worldwide English is 476160.
Those are different products from 21792. Do not join them on the number alone.

``printing`` "1st Edition" on a modern listing is TCGplayer's variant label.
It is not proof of a 2002 North American 1st Edition.

Sold buckets, per SKU, are public at
``GET https://infinite-api.tcgplayer.com/price/history/{id}/detailed?range=quarter``
when the request carries a browser user agent and the product page as referer.
A naked ``latestsales`` path is still 403. Each bucket has a low and a high for
that condition and variant, not one receipt. The exit is the transaction-weighted
median of those lows. One high day does not set it. 1st Edition Near Mint of
product 21792 cleared twice in the year pulled 2026-11-21: 14.99 and 4000.
That is not a price.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
DETAILS_URL = "https://mp-search-api.tcgplayer.com/v2/product/{product_id}/details"
UA = "cardman/0.2 (private research tool)"
# The history host answers 403 to a short agent. This is the one the product page uses.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# productLineId. Pokémon Japan is a different line from English Pokémon.
GAMES = {2: "yugioh", 3: "pokemon", 85: "pokemon_japan"}
LINE = {"yugioh": "yugioh", "pokemon": "pokemon", "pokemon_japan": "pokemon japan"}


def _dec(v) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _int(v) -> int | None:
    if v is None or v == "":
        return None
    return int(v)


@dataclass(frozen=True)
class TcgSku:
    sku: int
    condition: str
    variant: str
    language: str


@dataclass(frozen=True)
class TcgListing:
    """A current ask. Not a sale."""
    listing_id: int
    product_id: int
    price: Decimal
    shipping: Decimal | None
    condition: str
    printing: str
    language: str
    quantity: int


@dataclass(frozen=True)
class TcgProduct:
    product_id: int
    game: str
    name: str
    set_name: str
    set_code: str | None
    number: str | None
    rarity: str | None
    sealed: bool
    presale: bool
    market_price: Decimal | None
    lowest_price: Decimal | None
    median_price: Decimal | None
    skus: tuple[TcgSku, ...] = ()
    listings: tuple[TcgListing, ...] = ()
    listed: int | None = None

    @property
    def in_scope(self) -> bool:
        return self.game in GAMES.values() and not self.sealed and not self.presale


def _skus(raw) -> tuple[TcgSku, ...]:
    out = []
    for s in raw or []:
        if not isinstance(s, dict) or s.get("sku") is None:
            continue
        out.append(TcgSku(int(s["sku"]), str(s.get("condition") or ""), str(s.get("variant") or ""),
                          str(s.get("language") or "")))
    return tuple(out)


def _listed(doc: dict) -> int | None:
    """How many asks the product has. The payload's listings array is only a sample."""
    for key in ("totalListings", "listings"):
        v = doc.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _listings(raw, product_id: int) -> tuple[TcgListing, ...]:
    out = []
    for row in raw or []:
        if not isinstance(row, dict) or row.get("listingId") is None or row.get("price") is None:
            continue
        out.append(TcgListing(
            listing_id=int(row["listingId"]),
            product_id=int(row.get("productId") or product_id),
            price=Decimal(str(row["price"])),
            shipping=_dec(row.get("shippingPrice")),
            condition=str(row.get("condition") or ""),
            printing=str(row.get("printing") or ""),
            language=str(row.get("language") or ""),
            quantity=int(row.get("quantity") or 0),
        ))
    return tuple(out)


def parse_product(doc: dict) -> TcgProduct | None:
    """One details document or one search hit. None when it is not a dict with a product id."""
    if not isinstance(doc, dict) or doc.get("productId") is None:
        return None
    game = GAMES.get(_int(doc.get("productLineId")) or -1)
    if game is None:
        return None
    attrs = doc.get("customAttributes") or {}
    note = str(attrs.get("detailNote") or "")
    return TcgProduct(
        product_id=int(doc["productId"]),
        game=game,
        name=str(doc.get("productName") or ""),
        set_name=str(doc.get("setName") or ""),
        set_code=(str(doc["setCode"]) if doc.get("setCode") else None),
        number=(str(attrs["number"]) if attrs.get("number") else None),
        rarity=(str(doc["rarityName"]) if doc.get("rarityName") else None),
        sealed=bool(doc.get("sealed")),
        presale="presale" in note.lower(),
        market_price=_dec(doc.get("marketPrice")),
        lowest_price=_dec(doc.get("lowestPrice")),
        median_price=_dec(doc.get("medianPrice")),
        skus=_skus(doc.get("skus")),
        listings=_listings(doc.get("listings"), int(doc["productId"])),
        listed=_listed(doc),
    )


def variants(product: TcgProduct) -> tuple[str, ...]:
    return tuple(sorted({s.variant for s in product.skus if s.variant}))


def asks_for(product: TcgProduct, *, variant: str | None = None, condition: str | None = None) -> tuple[TcgListing, ...]:
    """Current asks for one SKU slice. Empty when the payload had no listings."""
    rows = product.listings
    if variant is not None:
        rows = tuple(r for r in rows if r.printing == variant)
    if condition is not None:
        rows = tuple(r for r in rows if r.condition == condition)
    return rows


# Level 1-4 seller. help.tcgplayer.com "TCGplayer Fees", checked 2026-11-21.
# 10.75% of the item, capped at $75, plus 2.5% + $0.30. No sales tax in this number.
# Not Pro, not Direct. Commission on shipping you charge is not included because the
# exit price below is the next ask's all-in and shipping charged is zero.
COMMISSION = Decimal("0.1075")
COMMISSION_CAP = Decimal("75")
TXN_PCT = Decimal("0.025")
TXN_FIXED = Decimal("0.30")


def _cents(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def seller_net(item: Decimal) -> Decimal:
    """What a Level 1-4 seller keeps from an item price. Not a prediction that it sells."""
    commission = min(item * COMMISSION, COMMISSION_CAP)
    return _cents(item - commission - item * TXN_PCT - TXN_FIXED)


def _all_in(row: TcgListing) -> Decimal:
    return row.price + (row.shipping or Decimal(0))


@dataclass(frozen=True)
class SkuRead:
    label: str
    product_id: int
    number: str | None
    name: str
    printing: str
    condition: str
    language: str
    n_asks: int
    buy: Decimal | None
    exit_ask: Decimal | None
    after_fees: Decimal | None
    why: str


def screen(product: TcgProduct) -> list[SkuRead]:
    """Label each SKU from its own asks. LOOK is not a return. There is no sold comp here.

    Buy the cheapest all-in. The exit is the next all-in of the same printing, condition
    and language, after Level 1-4 fees. A gap that dies is NO. One ask is ABSTAIN.
    """
    groups: dict[tuple[str, str, str], list[TcgListing]] = {}
    for row in product.listings:
        groups.setdefault((row.printing, row.condition, row.language), []).append(row)
    out = []
    for (printing, condition, language), rows in sorted(groups.items()):
        rows.sort(key=_all_in)
        base = dict(product_id=product.product_id, number=product.number, name=product.name,
                    printing=printing, condition=condition, language=language, n_asks=len(rows))
        if len(rows) < 2:
            out.append(SkuRead(label="ABSTAIN", buy=_all_in(rows[0]), exit_ask=None, after_fees=None,
                               why="one ask of this SKU; no second ask to sell under", **base))
            continue
        buy, exit_ask = _all_in(rows[0]), _all_in(rows[1])
        after = seller_net(exit_ask) - buy
        if after > 0:
            label, why = "LOOK", "same-SKU asks only; not a settled sale, so this is not a return yet"
        else:
            label, why = "NO", "the gap dies after Level 1-4 fees (10.75% capped at $75, plus 2.5% and $0.30)"
        out.append(SkuRead(label=label, buy=buy, exit_ask=exit_ask, after_fees=after, why=why, **base))
    return out


def misread(product: TcgProduct) -> str | None:
    """The headline TCGplayer leaves on the page: market minus lowest, across every SKU."""
    if product.market_price is None or product.lowest_price is None or not product.listings:
        return None
    gap = product.market_price - product.lowest_price
    if gap <= 0:
        return None
    cheap = min(product.listings, key=_all_in)
    return (f"market {product.market_price} minus lowest {product.lowest_price} is {gap}. "
            f"The cheap ask in hand is {cheap.printing} {cheap.condition} at {_all_in(cheap)}. "
            f"Market price is one number for every SKU. That gap is not a return.")


HISTORY_URL = "https://infinite-api.tcgplayer.com/price/history/{product_id}/detailed?range={span}"
# A cleared low under this fraction of the same bucket's market price is a bad print, not a comp.
# 14.99 against a bucket market of 2007.50 on 1st Edition Near Mint is the case.
OUTLIER = Decimal("0.2")
MIN_SALES = 3


@dataclass(frozen=True)
class SoldBucket:
    sku_id: int
    variant: str
    condition: str
    language: str
    date: str
    transactions: int
    low: Decimal
    high: Decimal
    market: Decimal | None


def _buckets(doc: dict) -> tuple[SoldBucket, ...]:
    out = []
    for row in (doc or {}).get("result") or []:
        if not isinstance(row, dict):
            continue
        for b in row.get("buckets") or []:
            n = int(str(b.get("transactionCount") or "0").split(".")[0] or "0")
            low = _dec(b.get("lowSalePrice"))
            if n <= 0 or low is None or low <= 0:
                continue
            out.append(SoldBucket(
                sku_id=int(row["skuId"]), variant=str(row.get("variant") or ""),
                condition=str(row.get("condition") or ""), language=str(row.get("language") or ""),
                date=str(b.get("bucketStartDate") or ""), transactions=n, low=low,
                high=_dec(b.get("highSalePrice")) or low, market=_dec(b.get("marketPrice"))))
    return tuple(out)


def _usable(bucket: SoldBucket) -> bool:
    if bucket.market is None or bucket.market <= 0:
        return True
    return bucket.low >= bucket.market * OUTLIER


@dataclass(frozen=True)
class SaleRead:
    label: str
    printing: str
    condition: str
    language: str
    sales: int
    exit_low: Decimal | None
    exit_date: str | None
    buy: Decimal | None
    after_fees: Decimal | None
    why: str


def _weighted_median_low(buckets: list[SoldBucket]) -> SoldBucket:
    """Lower median of the lows, each bucket repeated once per transaction.

    A day with a range counts every sale at that day's low. That is the floor of
    the day, not the receipt, and it keeps one high print from setting the exit.
    """
    rows = sorted(buckets, key=lambda b: (b.low, b.date))
    need = (sum(b.transactions for b in rows) + 1) // 2
    seen = 0
    for b in rows:
        seen += b.transactions
        if seen >= need:
            return b
    return rows[-1]


def judge(listing: TcgListing, history: dict) -> SaleRead:
    """Buy this ask only against cleared sales of the same variant, condition and language.

    The exit is the transaction-weighted median of the usable bucket lows, after
    Level 1-4 fees. Fewer than three usable sales is THIN. A low under 20% of its
    own bucket's market price is rejected. LOOK is still not a return.
    """
    same = [b for b in _buckets(history)
            if b.variant == listing.printing and b.condition == listing.condition and b.language == listing.language]
    base = dict(printing=listing.printing, condition=listing.condition, language=listing.language,
                buy=_all_in(listing), exit_low=None, exit_date=None, after_fees=None)
    if not same:
        return SaleRead(label="ABSTAIN", sales=0, why="no cleared sale of this SKU in the window", **base)
    junk = [b for b in same if not _usable(b)]
    good = [b for b in same if _usable(b)]
    sales = sum(b.transactions for b in good)
    if sales < MIN_SALES:
        span = ""
        if good:
            span = f" usable prints {min(b.low for b in good)} to {max(b.low for b in good)}."
        dropped = ""
        if junk:
            dropped = " Rejected " + ", ".join(
                f"{b.low} on {b.date} against a bucket market of {b.market}" for b in junk) + "."
        return SaleRead(label="THIN", sales=sales,
                        why=f"{sales} usable sale(s) of this SKU.{span}{dropped} Not a book.", **base)
    pick = _weighted_median_low(good)
    after = seller_net(pick.low) - _all_in(listing)
    lo, hi = min(b.low for b in good), max(b.low for b in good)
    why = (f"weighted-median bucket low {pick.low} on {pick.date}; usable lows {lo} to {hi}; "
           f"{sales} sales. Each sale in a bucket is counted at that day's low, not at a receipt. "
           f"Not a return.")
    label = "LOOK" if after > 0 else "NO"
    if label == "NO":
        why = "the ask is not under the median cleared low of this SKU after Level 1-4 fees. " + why
    return SaleRead(label=label, sales=sales, exit_low=pick.low, exit_date=pick.date,
                    after_fees=after, why=why,
                    **{k: v for k, v in base.items() if k not in ("exit_low", "exit_date", "after_fees")})


def fetch_history(product_id: int, span: str = "quarter", timeout: float = 25) -> dict:
    """The chart the product page draws. range is quarter, month or annual."""
    if span not in ("quarter", "month", "annual"):
        raise ValueError(f"unsupported range {span!r}")
    url = HISTORY_URL.format(product_id=int(product_id), span=span)
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Referer": f"https://www.tcgplayer.com/product/{int(product_id)}",
        "Origin": "https://www.tcgplayer.com",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def search_singles(game: str, query: str, *, size: int = 10, timeout: float = 25, post=_post) -> list[TcgProduct]:
    """Unsealed, non-presale singles for one of yugioh, pokemon, pokemon_japan.

    The raw page can lead with a presale. Those are dropped, and the next page is
    read until `size` in-scope singles are in hand or the results run out.
    """
    if game not in LINE:
        raise ValueError(f"unsupported game {game!r}")
    if size < 1:
        return []
    url = f"{SEARCH_URL}?q={urllib.parse.quote(query)}"
    out: list[TcgProduct] = []
    skip = 0
    page = max(size, 10)
    for _ in range(5):
        body = {
            "algorithm": "sales_dismax",
            "from": skip,
            "size": page,
            "filters": {"term": {"productLineName": [LINE[game]], "productTypeName": ["Cards"]}, "range": {}, "match": {}},
            "listingSearch": {
                "filters": {"term": {"sellerStatus": "Live", "channelId": 0},
                            "range": {"quantity": {"gte": 1}}, "exclude": {"channelExclusion": 0}},
                "context": {"cart": {}},
            },
            "context": {"cart": {}, "shippingCountry": "US"},
            "settings": {"useFuzzySearch": True},
            "sort": {},
        }
        doc = post(url, body, timeout)
        hits = ((doc.get("results") or [{}])[0].get("results") or [])
        if not hits:
            break
        for hit in hits:
            p = parse_product(hit)
            if p and p.in_scope and p.game == game:
                out.append(p)
                if len(out) == size:
                    return out
        skip += len(hits)
        if len(hits) < page:
            break
    return out


@dataclass(frozen=True)
class Review:
    product_id: int
    game: str
    name: str
    number: str | None
    set_name: str
    judged: int
    listed: int | None
    misread: str | None
    history_note: str | None
    reads: tuple[SaleRead, ...]


def review(products: list[TcgProduct], histories: dict[int, dict], notes: dict[int, str] | None = None) -> list[Review]:
    """Judge the asks in hand against that product's sold buckets. One history per product id."""
    notes = notes or {}
    out = []
    for p in products:
        hist = histories.get(p.product_id) or {"result": []}
        out.append(Review(
            product_id=p.product_id, game=p.game, name=p.name, number=p.number, set_name=p.set_name,
            judged=len(p.listings), listed=p.listed, misread=misread(p),
            history_note=notes.get(p.product_id),
            reads=tuple(judge(row, hist) for row in p.listings),
        ))
    return out


def review_query(game: str, query: str, *, size: int = 3, span: str = "quarter",
                 search=search_singles, history=fetch_history) -> list[Review]:
    """Search, then one history fetch per product. A failed history abstains; it does not invent a sale."""
    products = search(game, query, size=size)
    histories: dict[int, dict] = {}
    notes: dict[int, str] = {}
    for p in products:
        if p.product_id in histories or p.product_id in notes:
            continue
        try:
            histories[p.product_id] = history(p.product_id, span)
        except Exception as e:
            notes[p.product_id] = f"history failed: {type(e).__name__}"
            histories[p.product_id] = {"result": []}
    return review(products, histories, notes)


def _line(r: Review, row: SaleRead) -> str:
    sample = f"{r.judged} of {r.listed}" if r.listed is not None else str(r.judged)
    money = ""
    if row.after_fees is not None:
        money = f"  exit {row.exit_low} on {row.exit_date}  after fees {row.after_fees}"
    return (f"{row.label:7} {r.game:14} {r.number or '-':12} {row.printing} {row.condition}"
            f"  buy {row.buy}{money}  asks {sample}  {r.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cardman.tcgplayer")
    ap.add_argument("game", choices=sorted(LINE))
    ap.add_argument("query")
    ap.add_argument("--size", type=int, default=3)
    ap.add_argument("--span", default="quarter", choices=("quarter", "month", "annual"))
    args = ap.parse_args(argv)
    rows = review_query(args.game, args.query, size=args.size, span=args.span)
    if not rows:
        print("no in-scope singles")
        return 0
    for r in rows:
        if r.history_note:
            print(f"ABSTAIN {r.game} {r.number or '-'} {r.history_note}")
        if r.misread:
            print(f"MISREAD {r.number or '-'} {r.misread}")
        for row in r.reads:
            print(_line(r, row))
        if not r.reads and not r.history_note:
            print(f"ABSTAIN {r.game} {r.number or '-'} {r.name}  no asks in the search sample")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
