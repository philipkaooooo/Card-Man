"""TCGplayer public payload, captured 2026-11-21. Prices here are asks and aggregates, not sales."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from cardman.tcgplayer import (
    asks_for, judge, misread, parse_product, screen, search_singles, seller_net, variants)

DATA = Path(__file__).parent / "data" / "tcgplayer"


def load(name):
    return json.loads((DATA / name).read_text())


def test_lob001_market_price_is_not_a_1st_edition_price():
    p = parse_product(load("lob001_details.json"))
    assert p.product_id == 21792 and p.game == "yugioh" and p.number == "LOB-001"
    assert p.set_name == "The Legend of Blue Eyes White Dragon"
    assert p.in_scope and not p.sealed
    assert variants(p) == ("1st Edition", "Unlimited")
    assert len(p.skus) == 10
    assert {s.condition for s in p.skus} == {
        "Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged"}
    # One number for every SKU. Not a sale, and not Near Mint 1st.
    assert p.market_price == Decimal("119.14")
    assert p.lowest_price == Decimal("15.0")


def test_a_damaged_unlimited_ask_is_not_the_near_mint_price():
    p = parse_product(load("lob001_search_hit.json"))
    nm = asks_for(p, variant="Unlimited", condition="Near Mint")
    dmg = asks_for(p, variant="Unlimited", condition="Damaged")
    assert [a.price for a in nm] == [Decimal("161.99")]
    assert min(a.price for a in dmg) == Decimal("19.87")
    assert asks_for(p, variant="1st Edition") == ()
    assert p.market_price != nm[0].price


def test_charizard_is_pokemon_holofoil_not_yugioh():
    p = parse_product(load("charizard_base_details.json"))
    assert p.game == "pokemon" and p.number == "004/102" and p.set_name == "Base Set"
    assert variants(p) == ("Holofoil",)
    assert p.market_price == Decimal("1001.05")
    assert p.in_scope


def test_sealed_other_games_and_presale_are_out_of_scope():
    base = load("lob001_details.json")
    sealed = dict(base, sealed=True)
    assert parse_product(sealed).in_scope is False
    magic = dict(base, productLineId=1)
    assert parse_product(magic) is None
    presale = dict(base, customAttributes={"number": "LOB-001", "detailNote": "This product is a Presale item."})
    assert parse_product(presale).presale is True and parse_product(presale).in_scope is False
    japan = dict(base, productLineId=85, productLineName="Pokemon Japan")
    assert parse_product(japan).game == "pokemon_japan"


def test_the_market_gap_on_lob001_is_a_misread_and_the_same_sku_gap_dies():
    p = parse_product(load("lob001_search_hit.json"))
    text = misread(p)
    assert text is not None and "not a return" in text and "Damaged" in text
    rows = { (r.printing, r.condition): r for r in screen(p) }
    damaged = rows[("Unlimited", "Damaged")]
    assert damaged.label == "NO" and damaged.after_fees < 0
    assert damaged.buy == Decimal("19.87") and damaged.exit_ask == Decimal("19.90")
    # one Near Mint ask: nothing to undercut
    assert rows[("Unlimited", "Near Mint")].label == "ABSTAIN"


def test_a_same_sku_gap_that_survives_fees_is_a_look_not_a_return():
    hit = load("lob001_search_hit.json")
    hit["listings"] = [
        {"listingId": 1, "productId": 21792, "price": 40, "shippingPrice": 0,
         "condition": "Near Mint", "printing": "1st Edition", "language": "English", "quantity": 1},
        {"listingId": 2, "productId": 21792, "price": 80, "shippingPrice": 0,
         "condition": "Near Mint", "printing": "1st Edition", "language": "English", "quantity": 1},
    ]
    row = screen(parse_product(hit))[0]
    assert row.label == "LOOK" and row.after_fees > 0 and "not a settled sale" in row.why


def test_commission_caps_at_75_dollars():
    # 10.75% of 1000 is 107.50. The cap keeps 75. Then 2.5% is 25, plus 0.30.
    assert seller_net(Decimal("1000")) == Decimal("899.70")
    assert seller_net(Decimal("100")) == Decimal("86.45")


def test_near_mint_unlimited_ask_is_above_the_median_cleared_low():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[0]
    assert listing.condition == "Near Mint" and listing.printing == "Unlimited"
    row = judge(listing, load("lob001_quarter.json"))
    assert row.label == "NO" and row.sales == 10
    # 108.95 is in the book. The median low is still 119.99, and the ask is above it.
    assert row.exit_low == Decimal("119.99") and row.exit_date == "2026-11-14"
    assert "108.95" in row.why and row.after_fees < 0


def test_one_high_damaged_day_does_not_set_the_exit():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[1]
    assert listing.condition == "Damaged" and listing.price == Decimal("19.87")
    row = judge(listing, load("lob001_quarter.json"))
    # Five of the ten sales cleared at 19. The 26.60 day is real and is not the exit.
    assert row.label == "NO" and row.exit_low == Decimal("19") and row.sales == 10
    assert row.after_fees == Decimal("-3.69")
    assert "26.6" in row.why


def test_the_median_low_is_the_exit_when_one_bucket_is_high():
    listing = parse_product(load("lob001_search_hit.json")).listings[1]
    listing = type(listing)(**{**listing.__dict__, "price": Decimal("10"), "shipping": Decimal("0"),
                               "condition": "Near Mint", "printing": "Unlimited"})
    history = {"result": [{"skuId": "1", "variant": "Unlimited", "condition": "Near Mint", "language": "English",
                           "buckets": [
                               {"bucketStartDate": "2026-11-01", "transactionCount": "1", "lowSalePrice": "40",
                                "highSalePrice": "40", "marketPrice": "40"},
                               {"bucketStartDate": "2026-11-02", "transactionCount": "1", "lowSalePrice": "40",
                                "highSalePrice": "40", "marketPrice": "40"},
                               {"bucketStartDate": "2026-11-03", "transactionCount": "1", "lowSalePrice": "100",
                                "highSalePrice": "100", "marketPrice": "40"},
                           ]}]}
    row = judge(listing, history)
    assert row.label == "LOOK" and row.exit_low == Decimal("40") and row.sales == 3
    assert row.after_fees == Decimal("24.40")
    assert "not a return" in row.why.lower()


def test_two_1st_edition_prints_a_year_apart_are_not_a_book():
    hit = load("lob001_search_hit.json")
    # Same dollars as the Near Mint Unlimited ask, but the SKU is 1st Edition.
    listing = parse_product(hit).listings[0]
    listing = type(listing)(**{**listing.__dict__, "printing": "1st Edition"})
    row = judge(listing, load("lob001_1st_nm_annual.json"))
    assert row.label == "THIN" and row.sales == 1 and row.after_fees is None
    assert "14.99" in row.why and "Not a book" in row.why
    assert "Rejected" in row.why


def test_a_1st_edition_ask_does_not_use_unlimited_sales():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[0]
    listing = type(listing)(**{**listing.__dict__, "printing": "1st Edition"})
    row = judge(listing, load("lob001_quarter.json"))
    assert row.label == "ABSTAIN" and row.sales == 0
    hit = load("lob001_search_hit.json")
    sealed = dict(hit, productId=1, sealed=True, listings=[])
    magic = dict(hit, productId=2, productLineId=1, listings=[])
    payload = {"results": [{"results": [hit, sealed, magic]}]}

    def post(url, body, timeout):
        assert "yugioh" in url or body["filters"]["term"]["productLineName"] == ["yugioh"]
        return payload

    got = search_singles("yugioh", "Blue-Eyes White Dragon", post=post)
    assert [p.product_id for p in got] == [21792]
    with pytest.raises(ValueError):
        search_singles("magic", "x", post=post)
