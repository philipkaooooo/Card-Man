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
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
DETAILS_URL = "https://mp-search-api.tcgplayer.com/v2/product/{product_id}/details"
UA = "cardman/0.2 (private research tool)"

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


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def search_singles(game: str, query: str, *, size: int = 10, timeout: float = 25, post=_post) -> list[TcgProduct]:
    """Unsealed, non-presale singles for one of yugioh, pokemon, pokemon_japan."""
    if game not in LINE:
        raise ValueError(f"unsupported game {game!r}")
    body = {
        "algorithm": "sales_dismax",
        "from": 0,
        "size": size,
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
    doc = post(f"{SEARCH_URL}?q={urllib.parse.quote(query)}", body, timeout)
    block = (doc.get("results") or [{}])[0]
    out = []
    for hit in block.get("results") or []:
        p = parse_product(hit)
        if p and p.in_scope and p.game == game:
            out.append(p)
    return out
