from cardman.identity import ListingSignals, parse_set_code, resolve
from cardman.models import IdentityStatus, Region
from tests.fixtures import CATALOG, P_JP_STARTER, P_LOB_1ST, STRUCT_QCSR


def r(**kw):
    return resolve(ListingSignals(**kw), CATALOG)


def test_lob_first_vs_unlimited_abstains_without_stamp_photo():
    res = r(title="Blue-Eyes White Dragon LOB-EN001 Ultra Rare 1st Edition", printed_set_code="LOB-EN001",
            seller_edition="1st")
    assert res.status == IdentityStatus.CANDIDATES
    assert len(res.candidates) == 2
    assert any("edition stamp" in e for e in res.evidence_needed)


def test_lob_first_resolves_with_verified_stamp():
    res = r(title="Blue-Eyes White Dragon", printed_set_code="LOB-EN001", verified_edition="1st")
    assert res.status == IdentityStatus.RESOLVED and res.printing == P_LOB_1ST


def test_seller_typed_code_alone_never_resolves():
    res = r(title="Blue-Eyes White Dragon", seller_set_code="LOB-EN001", verified_edition="1st")
    assert res.status == IdentityStatus.CANDIDATES
    assert "set code only seller-entered" in res.blocking_reasons


def test_japanese_vintage_without_set_code_is_first_class():
    res = r(title="青眼の白龍 STARTER BOX ウルトラ", card_language="ja")
    assert res.status == IdentityStatus.RESOLVED and res.printing == P_JP_STARTER


def test_jp_listing_never_matches_en_tcg():
    res = r(title="Blue-Eyes White Dragon", card_language="ja", printed_set_code="LOB-EN001")
    assert res.status == IdentityStatus.CONFLICT


def test_same_code_multiple_rarities_needs_angle_shot():
    res = r(title="Fixture Card A", printed_set_code="STRC-EN001", seller_rarity="Quarter Century Secret Rare")
    assert res.status == IdentityStatus.CANDIDATES
    assert any("angled photo" in e for e in res.evidence_needed)
    ok = r(title="Fixture Card A", printed_set_code="STRC-EN001", verified_rarity="Quarter Century Secret Rare")
    assert ok.status == IdentityStatus.RESOLVED and ok.printing == STRUCT_QCSR


def test_asian_english_is_quarantined_not_tcg():
    res = r(title="Fixture Card A English", printed_set_code="STRC-AE001", card_language="en")
    assert res.status == IdentityStatus.QUARANTINED
    assert all(p.region == Region.ASIAN_EN_OCG_QUARANTINE for p in res.candidates)


def test_rush_duel_is_separate_format():
    assert r(title="Blue-Eyes White Dragon", printed_set_code="RD/KP01-JP000").status == IdentityStatus.QUARANTINED
    assert parse_set_code("RD/KP01-JP000")["game_format"].value == "RUSH_DUEL"


def test_slab_cert_not_mistaken_for_set_code():
    res = r(title="PSA 9 Blue-Eyes White Dragon", seller_set_code="81234567", slab_cert="81234567")
    assert any("cert number" in b for b in res.blocking_reasons)
    assert res.status != IdentityStatus.RESOLVED


def test_seller_typo_matches_but_does_not_resolve():
    res = r(title="Blue Eyes Whte Dragon LOB 1st", printed_set_code="LOB-EN001", verified_edition="1st")
    assert res.status == IdentityStatus.CANDIDATES
    assert res.candidates == [P_LOB_1ST]


def test_binder_photo_abstains():
    res = r(title="Binder lot Blue-Eyes Dark Magician", is_multi_card=True)
    assert res.status == IdentityStatus.CANDIDATES and not res.candidates


def test_passcode_is_not_a_printing_id():
    res = r(title="Blue-Eyes White Dragon 89631139")
    assert res.status == IdentityStatus.CANDIDATES and len(res.candidates) == 3
