"""Exact identification on real printings (YAML Yugi pinned subset): seller errors, traps, edition rules."""
from dataclasses import replace

from cardman.identity import Catalog, ListingSignals, resolve
from cardman.intel import analyze, lot_contents
from cardman.listing_parser import parse_listing_text as P
from cardman.models import IdentityStatus
from cardman.print_dates import PrintDates, code_key
from tests.real_catalog import real_catalog

CATALOG = real_catalog()
RULES = {"Legend of Blue Eyes White Dragon": ["1st", "Unlimited"]}


def test_parser_reads_japanese_english_and_taiwanese_terms():
    p = P("遊戯王 青眼の白龍 LOB-EN001 ウルトラレア 1st 美品 PSA10 cert 12345678")
    assert p.set_codes == ("LOB-EN001",) and p.rarities == ("Ultra Rare",) and p.editions == ("1st",)
    assert (p.grade_company, p.grade, p.cert, p.condition) == ("PSA", "10", "12345678", "EX")
    assert P("遊戲王 日版 金亮 青眼").rarities == ("Ultra Rare",) and P("日版").language == "ja"
    assert P("レリーフ 青眼").rarities == ("Ultimate Rare",)
    assert P("クオシク ブラマジ").rarities == ("Quarter Century Secret Rare",)


def test_parser_does_not_turn_longer_rarities_or_grades_into_false_hits():
    assert P("Ultra Parallel Rare").rarities == ("Ultra Parallel Rare",)
    assert P("Prismatic Secret Rare").rarities == ("Prismatic Secret Rare",)
    assert P("PSA-10 Blue-Eyes").set_codes == ()
    assert P("BLUE-EYES white dragon").set_codes == ()


def test_parser_flags_lots_fakes_accessories_and_unsure_sellers():
    assert P("遊戯王 引退 まとめ 500枚").is_lot and P("遊戯王 引退 まとめ 500枚").lot_size == 500
    assert P("Blue-Eyes proxy orica").counterfeit_cues == ("proxy", "orica")
    assert "スリーブ" in P("青眼 スリーブ").not_a_card_cues
    assert P("実家から出てきた 詳細不明").ignorance_cues == ("詳細不明", "実家")


def test_counterfeit_language_blocks():
    a = analyze("Blue-Eyes White Dragon LOB-EN001 proxy", CATALOG)
    assert a.blocked and a.has("COUNTERFEIT_CUE")


def test_real_code_on_wrong_card_name_is_caught():
    # LOB-EN005 is Dark Magician, not Blue-Eyes.
    a = analyze("Blue-Eyes White Dragon LOB-EN005", CATALOG)
    assert a.has("CODE_NAME_MISMATCH")
    assert any("Dark Magician" in f.text for f in a.findings if f.kind == "CODE_NAME_MISMATCH")


def test_rarity_the_code_never_had_is_caught():
    a = analyze("Blue-Eyes White Dragon LOB-EN001 Secret Rare", CATALOG)
    assert a.has("RARITY_NOT_ON_CODE")


def test_shared_code_reprint_trap_is_named():
    a = analyze("Blue-Eyes White Dragon LOB-EN001", CATALOG)
    assert a.has("REPRINT_SHARES_CODE") and a.identity.status == IdentityStatus.CANDIDATES


def test_seller_claims_shift_weights_but_never_resolve():
    a = analyze("Blue-Eyes White Dragon LOB-EN001 1st Edition Ultra Rare", CATALOG)
    assert a.identity.status != IdentityStatus.RESOLVED
    assert abs(sum(a.weights.values()) - 1) < 1e-9


def test_photo_text_that_disagrees_with_seller_is_an_upside_signal_not_proof():
    a = analyze("Blue-Eyes White Dragon LOB-EN001", CATALOG, ocr_codes=("LOB-EN005",))
    assert a.has("PHOTO_CONTRADICTS_SELLER") and a.identity.status != IdentityStatus.RESOLVED


def test_edition_rule_resolves_only_a_verified_stamp_on_a_listed_product():
    cat = Catalog(list(CATALOG.families.values()), CATALOG.printings, edition_rules=RULES)
    base = dict(title="Blue-Eyes White Dragon LOB-EN001", printed_set_code="LOB-EN001",
                verified_product="Legend of Blue Eyes White Dragon")
    ok = resolve(ListingSignals(**base, verified_edition="1st"), cat)
    assert ok.status == IdentityStatus.RESOLVED and ok.printing.edition == "1st" and ok.printing.id.endswith("|ed:1st")
    # seller text is not a stamp
    assert resolve(ListingSignals(**base, seller_edition="1st"), cat).status == IdentityStatus.CANDIDATES
    # an edition the product does not have
    assert resolve(ListingSignals(**base, verified_edition="Limited"), cat).status == IdentityStatus.CANDIDATES
    # no rule, no resolution: the strict default is unchanged
    assert resolve(ListingSignals(**base, verified_edition="1st"), CATALOG).status == IdentityStatus.CANDIDATES


def test_lot_itemizer_finds_cards_and_keeps_them_as_claims():
    rows = lot_contents("Blue-Eyes White Dragon LOB-EN001 Ultra Rare\nDark Magician LOB-EN005\nrandom bulk", CATALOG)
    assert [r["code"] for r in rows] == ["LOB-EN001", "LOB-EN005"] and rows[0]["rarity_claim"] == "Ultra Rare"


def test_print_dates_separate_original_from_reprint(tmp_path):
    body = b'{"cardData":{"en":{"prints":[{"code":"LOB-001","date":"2002-03-08"},{"code":"SDK-001","date":"2002-03-29"}]}}}'
    calls = []

    def get(url):
        calls.append(url)
        return {"X-Cache-Revision": "7"}, body
    d = PrintDates(tmp_path / "yr.sqlite", get=get)
    assert code_key("LOB-EN001") == code_key("LOB-001")
    assert d.describe(4007, "LOB-EN001")["is_original"] is True
    assert d.describe(4007, "SDK-E001")["is_original"] is False
    assert d.describe(4007, "XXX-EN001")["is_original"] is None
    assert len(calls) == 1      # cached, as the API asks
