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
import html
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
DETAILS_URL = "https://mp-search-api.tcgplayer.com/v2/product/{product_id}/details"
LISTINGS_URL = "https://mp-search-api.tcgplayer.com/v1/product/{product_id}/listings?mpfev=5762"
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
    direction: str = ""
    prior_low: Decimal | None = None
    recent_low: Decimal | None = None
    series: tuple[tuple[str, Decimal, int], ...] = ()


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


def _halves(buckets: list[SoldBucket]) -> tuple[list[SoldBucket], list[SoldBucket]] | None:
    """Newer half of the transactions, then the older half. None when either side has under two."""
    total = sum(b.transactions for b in buckets)
    need = total // 2
    if need < 2 or total - need < 2:
        return None
    recent, prior = [], []
    got = 0
    for b in sorted(buckets, key=lambda b: b.date, reverse=True):
        if got >= need:
            prior.append(b)
            continue
        room = need - got
        if b.transactions <= room:
            recent.append(b)
            got += b.transactions
        else:
            recent.append(SoldBucket(b.sku_id, b.variant, b.condition, b.language, b.date, room, b.low, b.high, b.market))
            prior.append(SoldBucket(b.sku_id, b.variant, b.condition, b.language, b.date,
                                    b.transactions - room, b.low, b.high, b.market))
            got += room
    return recent, prior


def _trend(buckets: list[SoldBucket]) -> tuple[str, Decimal | None, Decimal | None]:
    """DOWN only when the newer half of clears is at least 5% under the older half."""
    halves = _halves(buckets)
    if halves is None:
        return "", None, None
    recent, prior = halves
    recent_low = _weighted_median_low(recent).low
    prior_low = _weighted_median_low(prior).low
    if recent_low <= prior_low * Decimal("0.95"):
        direction = "DOWN"
    elif recent_low >= prior_low * Decimal("1.05"):
        direction = "UP"
    else:
        direction = "FLAT"
    return direction, prior_low, recent_low


def judge(listing: TcgListing, history: dict) -> SaleRead:
    """Buy this ask only against cleared sales of the same variant, condition and language.

    The exit is the transaction-weighted median of the usable bucket lows, after
    Level 1-4 fees. A DOWN book uses the newer half instead, so an old high
    cannot keep a price the last sales have already left. An UP book does not
    get paid for the rise. Fewer than three usable sales is THIN. LOOK is not a return.
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
    series = tuple((b.date, b.low, b.transactions) for b in sorted(good, key=lambda b: b.date))
    if sales < MIN_SALES:
        span = ""
        if good:
            span = f" usable prints {min(b.low for b in good)} to {max(b.low for b in good)}."
        dropped = ""
        if junk:
            dropped = " Rejected " + ", ".join(
                f"{b.low} on {b.date} against a bucket market of {b.market}" for b in junk) + "."
        return SaleRead(label="THIN", sales=sales, series=series,
                        why=f"{sales} usable sale(s) of this SKU.{span}{dropped} Not a book.", **base)
    direction, prior_low, recent_low = _trend(good)
    pick = _weighted_median_low(good)
    if direction == "DOWN":
        pick = _weighted_median_low(_halves(good)[0])
    after = seller_net(pick.low) - _all_in(listing)
    lo, hi = min(b.low for b in good), max(b.low for b in good)
    move = ""
    if direction:
        move = f" {direction} {prior_low} to {recent_low}."
    why = (f"weighted-median bucket low {pick.low} on {pick.date}; usable lows {lo} to {hi}; "
           f"{sales} sales.{move} Each sale in a bucket is counted at that day's low, not at a receipt. "
           f"Not a return.")
    label = "LOOK" if after > 0 else "NO"
    if label == "NO":
        why = "the ask is not under the median cleared low of this SKU after Level 1-4 fees. " + why
    return SaleRead(label=label, sales=sales, exit_low=pick.low, exit_date=pick.date,
                    after_fees=after, why=why, direction=direction, prior_low=prior_low,
                    recent_low=recent_low, series=series,
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
    calls: tuple[Call, ...] = ()


def review(products: list[TcgProduct], histories: dict[int, dict], notes: dict[int, str] | None = None) -> list[Review]:
    """Judge the asks in hand against that product's sold buckets. One history per product id."""
    notes = notes or {}
    out = []
    for p in products:
        hist = histories.get(p.product_id) or {"result": []}
        failed = bool(notes.get(p.product_id))
        out.append(Review(
            product_id=p.product_id, game=p.game, name=p.name, number=p.number, set_name=p.set_name,
            judged=len(p.listings), listed=p.listed, misread=misread(p),
            history_note=notes.get(p.product_id),
            reads=tuple(judge(row, hist) for row in p.listings),
            calls=tuple(label_listing(p, row, None if failed else hist) for row in p.listings),
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


TWO = Decimal("2")
DECISIONS = ("2x", "buy", "watch", "pass")


@dataclass(frozen=True)
class Call:
    """What to do with one ask. 2x and buy are the only labels worth paying on."""
    label: str
    product_id: int
    listing_id: int
    name: str
    number: str | None
    printing: str
    condition: str
    language: str
    price: Decimal
    shipping: Decimal | None
    typed: bool
    sales: int
    exit_low: Decimal | None
    after_fees: Decimal | None
    multiple: Decimal | None
    why: str

    @property
    def card_key(self) -> str:
        return f"tcg:{self.product_id}|{self.printing}|{self.condition}|{self.language}"

    def row(self) -> dict:
        return {"source": "tcgplayer", "listing_id": self.listing_id, "product_id": self.product_id,
                "card_key": self.card_key, "name": self.name, "number": self.number, "printing": self.printing,
                "condition": self.condition, "language": self.language, "price": self.price,
                "shipping": self.shipping, "label": self.label, "sales": self.sales, "exit_low": self.exit_low,
                "after_fees": self.after_fees, "multiple": self.multiple, "why": self.why}


def label_listing(product: TcgProduct, listing: TcgListing, history: dict | None) -> Call:
    """2x if the cautious exit nets at least twice the all-in. buy if it only clears.

    Both need shipping on the ask and three usable sales of this printing, condition
    and language. A blank type is not stored. A blank shipping price is watch.
    """
    typed = bool(listing.printing and listing.condition and listing.language) and listing.product_id == product.product_id
    empty = dict(sales=0, exit_low=None, after_fees=None, multiple=None)
    if not typed:
        why = "printing, condition or language is blank, or the listing is a different product. Not stored."
        return Call("watch", product.product_id, listing.listing_id, product.name, product.number,
                    listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                    False, why=why, **empty)
    if history is None:
        return Call("watch", product.product_id, listing.listing_id, product.name, product.number,
                    listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                    True, why="Cleared sales were not pulled. An ask is not a sale.", **empty)
    sale = judge(listing, history)
    if sale.label == "NO":
        return Call("pass", product.product_id, listing.listing_id, product.name, product.number,
                    listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                    True, sale.sales, sale.exit_low, sale.after_fees, None, sale.why)
    if sale.label != "LOOK" or sale.exit_low is None or not sale.buy:
        return Call("watch", product.product_id, listing.listing_id, product.name, product.number,
                    listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                    True, sale.sales, sale.exit_low, None, None, sale.why)
    if listing.shipping is None:
        return Call("watch", product.product_id, listing.listing_id, product.name, product.number,
                    listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                    True, sale.sales, sale.exit_low, None, None,
                    "Shipping was blank, so the all-in is not known. Not a buy.")
    multiple = _cents(seller_net(sale.exit_low) / sale.buy)
    label = "2x" if multiple >= TWO else "buy"
    why = (f"After Level 1-4 fees the cautious exit returns {multiple} times the all-in. " if label == "2x"
           else "The all-in clears the cautious exit after fees and is not a double. ")
    return Call(label, product.product_id, listing.listing_id, product.name, product.number,
                listing.printing, listing.condition, listing.language, listing.price, listing.shipping,
                True, sale.sales, sale.exit_low, sale.after_fees, multiple, why + sale.why)


def _listing_query(start: int, size: int) -> dict:
    return {
        "from": start, "size": size,
        "filters": {"term": {"sellerStatus": "Live", "channelId": 0},
                    "range": {"quantity": {"gte": 1}}, "exclude": {"channelExclusion": 0}},
        "context": {"cart": {}, "shippingCountry": "US"},
        "sort": {"field": "price+shipping", "order": "asc"},
    }


def fetch_listings(product_id: int, *, post=None, page_size: int = 50, timeout: float = 25
                   ) -> tuple[list[dict], int | None]:
    """Every ask this product's listings call returns. Another product's row is dropped.

    Stops on an empty page, a page that adds nothing, a short page, or the reported total.
    Asks that were not in the pull are not invented.
    """
    post = post or _post_browser
    seen: dict[int, dict] = {}
    total: int | None = None
    url = LISTINGS_URL.format(product_id=int(product_id))

    def ask(body: dict) -> dict:
        try:
            return post(url, body, timeout)
        except TypeError:
            return post(url, body)

    for page in range(40):
        block = (ask(_listing_query(page * page_size, page_size)).get("results") or [{}])[0]
        if total is None and block.get("totalResults") is not None:
            total = int(block["totalResults"])
        rows = [r for r in (block.get("results") or []) if isinstance(r, dict) and r.get("listingId") is not None]
        if not rows:
            break
        fresh = 0
        for row in rows:
            if int(row.get("productId") or product_id) != int(product_id):
                continue
            lid = int(row["listingId"])
            if lid in seen:
                continue
            seen[lid] = row
            fresh += 1
        if fresh == 0 or (total is not None and len(seen) >= total) or len(rows) < page_size:
            break
    return list(seen.values()), total


def _post_browser(url: str, body: dict, timeout: float = 25) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json", "Content-Type": "application/json",
                 "Referer": "https://www.tcgplayer.com/", "Origin": "https://www.tcgplayer.com"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_details(product_id: int, timeout: float = 25) -> dict:
    url = DETAILS_URL.format(product_id=int(product_id))
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json",
                                               "Referer": f"https://www.tcgplayer.com/product/{int(product_id)}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def assemble(details: dict, rows: list[dict], total: int | None) -> TcgProduct | None:
    doc = dict(details)
    if total is not None:
        doc["totalListings"] = total
    if not isinstance(doc.get("listings"), list):
        doc.pop("listings", None)
    doc["listings"] = rows
    return parse_product(doc)


def _line(r: Review, row: SaleRead, call: Call | None = None) -> str:
    sample = f"{r.judged} of {r.listed}" if r.listed is not None else str(r.judged)
    money = ""
    if row.after_fees is not None:
        money = f"  exit {row.exit_low} on {row.exit_date}  after fees {row.after_fees}"
    trend = f"  {row.direction} {row.prior_low}→{row.recent_low}" if row.direction else ""
    shown = call.label if call is not None else row.label
    head = (f"{shown:5} {r.game:14} {r.number or '-':12} {row.printing} {row.condition}"
            f"  buy {row.buy}{money}{trend}  asks {sample}  {r.name}")
    if not row.series:
        return head
    path = ", ".join(f"{d[5:]} {low}×{n}" for d, low, n in row.series)
    return head + "\n  " + path


def sku_svg(row: SaleRead, *, title: str) -> str:
    """The same SVG the review UI uses. Points are bucket lows. Lines are the ask and the exit."""
    from datetime import datetime, timezone
    from .charts import Point, Ref, price_chart
    pts = [Point(datetime.fromisoformat(d).replace(tzinfo=timezone.utc), float(low),
                 "MARKETPLACE_MARKED_SOLD", "used", f"{d} low {low} ×{n}")
           for d, low, n in row.series]
    refs = []
    if row.buy is not None:
        refs.append(Ref("ask", float(row.buy), "ask"))
    if row.exit_low is not None:
        refs.append(Ref("exit", float(row.exit_low), "exit"))
    return price_chart(pts, refs, currency="USD", label=title)


def page_html(rows: list[Review]) -> str:
    """One chart per judged SKU, and the same numbers in a table under it."""
    parts = [
        "<!doctype html><meta charset=utf-8><title>cardman</title>",
        "<style>.grid{stroke:#ddd}.axis{stroke:#888}.tick{fill:#444;font:11px sans-serif}"
        "body{font:14px sans-serif;margin:24px} table{border-collapse:collapse}"
        "td,th{padding:2px 8px;text-align:right} td.l,th.l{text-align:left}</style>",
    ]
    for r in rows:
        for row in r.reads:
            if not row.series:
                continue
            title = f"{r.name} {r.number or ''} {row.printing} {row.condition}".strip()
            parts.append(f"<h2>{html.escape(title)}</h2>")
            parts.append(f"<p>ask {row.buy} · exit {row.exit_low} · {html.escape(row.direction)} "
                         f"{row.prior_low} → {row.recent_low}</p>")
            parts.append(f"<p>{html.escape(row.why)}</p>")
            parts.append(sku_svg(row, title=title))
            parts.append("<table><tr><th class=l>date</th><th>bucket low</th><th>sales</th></tr>")
            for date, low, n in row.series:
                parts.append(f"<tr><td class=l>{html.escape(date)}</td><td>{low}</td><td>{n}</td></tr>")
            parts.append("</table>")
    return "\n".join(parts)


def pull(product_id: int, span: str, store_path: str) -> int:
    """Every ask for one product id, labeled, and stored when the type is complete."""
    from datetime import datetime, timezone

    from .store import Store

    details = fetch_details(product_id)
    if int(details.get("productId") or 0) != int(product_id):
        print(f"details were for {details.get('productId')}, not {product_id}")
        return 1
    rows, total = fetch_listings(product_id)
    product = assemble(details, rows, total)
    if product is None or not product.in_scope:
        print(f"{product_id} is not an in-scope single")
        return 1
    note = None
    try:
        history = fetch_history(product_id, span)
    except Exception as exc:
        history = None
        note = f"history failed: {type(exc).__name__}"
    order = {name: i for i, name in enumerate(DECISIONS)}
    calls = sorted((label_listing(product, row, history) for row in product.listings),
                   key=lambda c: (order[c.label], c.price))
    written, refused = Store(store_path).save_public_listings(
        [c.row() for c in calls if c.typed], datetime.now(timezone.utc))
    counts = {name: sum(c.label == name for c in calls) for name in DECISIONS}
    print(f"{product.product_id} {product.name} {product.number or ''} [{product.game}]".rstrip())
    print(f"site count {total if total is not None else 'unknown'}; captured {len(calls)}; stored {written}")
    if total is not None and len(calls) < total:
        print(f"{total - len(calls)} asks were not in the pull. They are not labeled.")
    if note:
        print(note)
    print(" ".join(f"{name} {counts[name]}" for name in DECISIONS))
    for c in calls:
        if c.label not in ("2x", "buy"):
            continue
        all_in = c.price + c.shipping if c.shipping is not None else None
        print(f"{c.label:4} {c.card_key} all-in {all_in} exit {c.exit_low} x{c.multiple} n={c.sales} listing {c.listing_id}")
        print(f"     {c.why}")
    watched = [c for c in calls if c.label == "watch"]
    if watched:
        print(f"watch {len(watched)}")
        for c in watched[:8]:
            print(f"     {c.printing} {c.condition} {c.language} {c.price}  {c.why}")
    for line in refused:
        print(f"refused {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cardman.tcgplayer")
    ap.add_argument("game", nargs="?", choices=sorted(LINE))
    ap.add_argument("query", nargs="?")
    ap.add_argument("--product", type=int, help="one product id: pull every ask, label it, store the typed rows")
    ap.add_argument("--store", default="data/store/evidence.sqlite")
    ap.add_argument("--size", type=int, default=3)
    ap.add_argument("--span", default="quarter", choices=("quarter", "month", "annual"))
    ap.add_argument("--html", help="write one chart per SKU, with the same numbers in a table")
    args = ap.parse_args(argv)
    if args.product:
        return pull(args.product, args.span, args.store)
    if not args.game or not args.query:
        ap.error("give a game and a query, or --product ID")
    rows = review_query(args.game, args.query, size=args.size, span=args.span)
    if not rows:
        print("no in-scope singles")
        return 0
    for r in rows:
        if r.history_note:
            print(f"ABSTAIN {r.game} {r.number or '-'} {r.history_note}")
        if r.misread:
            print(f"MISREAD {r.number or '-'} {r.misread}")
        for row, call in zip(r.reads, r.calls or r.reads):
            print(_line(r, row, call if r.calls else None))
        if not r.reads and not r.history_note:
            print(f"ABSTAIN {r.game} {r.number or '-'} {r.name}  no asks in the search sample")
    if args.html:
        open(args.html, "w").write(page_html(rows))
        print(f"wrote {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
