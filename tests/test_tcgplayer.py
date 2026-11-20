"""TCGplayer public payload, captured 2026-11-21. Prices here are asks and aggregates, not sales."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from cardman.tcgplayer import asks_for, parse_product, search_singles, variants

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


def test_search_keeps_only_in_scope_singles_of_the_requested_game():
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
