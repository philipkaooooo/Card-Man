"""Seller text -> signals and risk flags (EN and JP)."""
from cardman.listing_text import find_rarities, parse_listing


def test_english_title():
    r = parse_listing("Blue-Eyes White Dragon LOB-EN001 1st Edition Ultra Rare PSA 9")
    assert r.signals.seller_set_code == "LOB-EN001"
    assert r.rarity_claims == ["Ultra Rare"] and r.signals.seller_rarity == "Ultra Rare"
    assert r.edition_claim == "1st" and (r.grade_company, r.grade) == ("PSA", "9")
    assert not r.is_lot and r.flags == []


def test_japanese_title_and_slang():
    r = parse_listing("青眼の白龍 レリーフ 美品 QCCU-JP001")
    assert r.signals.seller_set_code == "QCCU-JP001"
    assert r.rarity_claims == ["Ultimate Rare"] and r.condition == "LP" and r.language_hint == "ja"
    assert find_rarities("灰流うらら 25thシク クオシク") == ["Quarter Century Secret Rare"]
    assert find_rarities("ウルパラ") == ["Ultra Parallel Rare"]


def test_longer_rarity_names_are_not_double_read():
    assert find_rarities("Ultra Parallel Rare") == ["Ultra Parallel Rare"]
    assert find_rarities("Quarter Century Secret Rare") == ["Quarter Century Secret Rare"]
    assert find_rarities("Super Rare SR01-EN001") == ["Super Rare"]   # SR01 is a set, not a rarity


def test_lots_and_collections():
    r = parse_listing("Ash Blossom MACR-EN036 Secret Rare x3 playset NM")
    assert r.is_lot and r.quantity == 3 and r.signals.is_multi_card and "lot" in r.flag_codes
    r = parse_listing("Yugioh binder collection vintage", "no returns, stock photo")
    assert {"collection", "no_returns", "stock_photo"} <= r.flag_codes
    assert not parse_listing("Dark Magician 1枚").is_lot


def test_risk_words():
    assert "proxy" in parse_listing("Blue Eyes White Dragon Orica custom").flag_codes
    assert "asian_english" in parse_listing("Ash Blossom Asian English SR").flag_codes
    assert {"stock_photo", "no_returns"} <= parse_listing("灰流うらら", "画像はイメージです 返品不可").flag_codes


def test_language_conflict():
    r = parse_listing("Blue-Eyes White Dragon LOB-EN001 Japanese")
    assert "language_conflict" in r.flag_codes
