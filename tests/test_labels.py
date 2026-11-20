"""LOB-001 and LOB-EN001 stay different cards, under the set names on the product page."""
from decimal import Decimal

from cardman.labels import explain, explain_details, show, sku_line, stated
from cardman.tcgplayer import Review, SaleRead, _line


def test_lob_001_is_one_card_and_not_the_en_code():
    text = explain("LOB-001")
    assert "The Legend of Blue Eyes White Dragon" in text
    assert "product 21792" in text
    assert "1st Edition or Unlimited" in text
    assert "Worldwide English" in text and "25th Anniversary" in text
    assert "Asian-English" in text
    assert "Legend of Blue Eyes White Dragon (Worldwide English)" not in text.split("It is not LOB-EN001")[0]


def test_lob_en001_is_two_cards_and_neither_is_21792():
    text = explain("LOB-EN001")
    assert "2 cards, not one" in text
    assert "product 476160" in text and "Worldwide English" in text
    assert "product 486054" in text and "25th Anniversary" in text
    assert "Unlimited only" in text
    assert "product 21792" in text
    assert "The Legend of Blue Eyes White Dragon" not in text.split("LOB-001 is not either")[0]


def test_a_sku_is_shown_by_the_official_set_not_the_storage_key():
    line = sku_line(21792, "Unlimited", "Damaged", "English")
    assert line == ("Blue-Eyes White Dragon · The Legend of Blue Eyes White Dragon · LOB-001 · "
                    "Unlimited · Damaged · English")
    assert show("tcg:486054|Unlimited|Near Mint|English") == (
        "Blue-Eyes White Dragon · Legend of Blue Eyes White Dragon (25th Anniversary Edition) · "
        "LOB-EN001 · Unlimited · Near Mint · English")
    assert show("tcg:21792|Unlimited|Damaged|English") != show("tcg:476160|Unlimited|Damaged|English")
    assert show("code:RDS-EN021|1st Edition|Near Mint|English").endswith("no product joined")


def test_a_product_id_search_uses_the_same_line():
    text = explain("476160")
    assert text == ("Blue-Eyes White Dragon · Legend of Blue Eyes White Dragon (Worldwide English) · "
                    "LOB-EN001 · product 476160 · Unlimited only · English")


def test_a_live_product_keeps_its_own_set_and_a_shared_number_is_not_collapsed():
    own = explain_details({
        "productId": 478098, "productName": "Radiant Charizard", "setName": "Crown Zenith",
        "customAttributes": {"number": "020/159"},
        "skus": [{"variant": "Holofoil", "language": "English", "condition": "Damaged"}],
    })
    assert own == "Radiant Charizard · Crown Zenith · 020/159 · product 478098 · Holofoil · English"
    assert "LOB" not in own
    shared = explain_details({
        "productId": 999, "productName": "Blue-Eyes White Dragon", "setName": "Some Other Set",
        "customAttributes": {"number": "LOB-EN001"},
        "skus": [{"variant": "Unlimited", "language": "English"}],
    })
    assert shared.startswith("Blue-Eyes White Dragon · Some Other Set · LOB-EN001 · product 999 · Unlimited · English")
    assert "product 476160" in shared and "product 486054" in shared
    assert stated("Blue-Eyes White Dragon", "", "LOB-EN001").endswith("the set name is required")


def test_a_judged_row_prints_the_set_not_just_the_number():
    row = SaleRead("NO", "Unlimited", "Damaged", "English", 10, Decimal("19"), "2026-11-18",
                   Decimal("19.87"), Decimal("-3.69"), "no")
    review = Review(21792, "yugioh", "Blue-Eyes White Dragon", "LOB-001",
                    "The Legend of Blue Eyes White Dragon", 1, 115, None, None, (row,))
    text = _line(review, row)
    assert "Blue-Eyes White Dragon · The Legend of Blue Eyes White Dragon · LOB-001 · Unlimited · Damaged · English" in text
