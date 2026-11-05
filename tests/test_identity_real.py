"""Regression traps on real documented printings (YAML Yugi, pinned revision; see tests/data/yaml_yugi).

Each wrong-printing case must stay out of RESOLVED, so it blocks Qualified downstream.
"""
from datetime import datetime, timezone
from decimal import Decimal

from cardman.identity import ListingSignals, resolve
from cardman.models import IdentityStatus, Region
from cardman.workflow import Stage, evaluate
from tests.real_catalog import PROVENANCE, real_catalog

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def r(**kw):
    return resolve(ListingSignals(**kw), real_catalog())


def ids(res):
    return {p.id for p in res.candidates}


def test_subset_is_pinned_and_identity_only():
    assert len(PROVENANCE["revision"]) == 40
    assert "no edition (1st/Unlimited/Limited) data" in PROVENANCE["limitations"]


def test_jp_vintage_bewd_without_code_abstains_across_starter_boxes():
    # Starter Box, Starter Box: Theatrical Release and EX Starter Box all list an Ultra Rare BEWD with no set code.
    res = r(title="青眼の白龍 ウルトラレア 初期", card_language="ja")
    assert res.status == IdentityStatus.CANDIDATES
    assert {"p:JP_OCG:-:starter-box:ultra-rare@89631139",
            "p:JP_OCG:-:starter-box-theatrical-release:ultra-rare@89631139",
            "p:JP_OCG:-:ex-starter-box:ultra-rare@89631139"} <= ids(res)
    assert all(p.region == Region.JP_OCG for p in res.candidates)
    assert any("product" in e for e in res.evidence_needed)


def test_jp_original_starter_box_resolves_only_with_product_evidence():
    res = r(title="青眼の白龍 初期", card_language="ja", verified_product="Starter Box", verified_rarity="Ultra Rare")
    assert res.status == IdentityStatus.RESOLVED
    assert res.printing.id == "p:JP_OCG:-:starter-box:ultra-rare@89631139"
    assert res.printing.set_code is None   # vintage JP without a printed code is a first-class record


def test_jp_reprint_with_old_style_code_resolves_by_catalog_region():
    # LB-01 has no language segment; region comes from the catalog, not from the code.
    res = r(title="青眼の白龍 LB-01", printed_set_code="LB-01", card_language="ja")
    assert res.status == IdentityStatus.RESOLVED
    assert res.printing.product_name == "Legend of Blue Eyes White Dragon (Japanese)"


def test_lob_en001_original_vs_25th_anniversary_reprint_abstains():
    res = r(title="Blue-Eyes White Dragon LOB-EN001", printed_set_code="LOB-EN001")
    assert res.status == IdentityStatus.CANDIDATES
    assert {p.product_name for p in res.candidates} == {
        "Legend of Blue Eyes White Dragon", "Legend of Blue Eyes White Dragon (25th Anniversary Edition)"}


def test_lob_en001_edition_stamp_still_blocked_without_edition_level_source():
    res = r(title="Blue-Eyes White Dragon LOB-EN001", printed_set_code="LOB-EN001", verified_edition="1st",
            verified_product="Legend of Blue Eyes White Dragon")
    assert res.status == IdentityStatus.CANDIDATES and len(res.candidates) == 1
    assert any("edition not documented" in b for b in res.blocking_reasons)


def test_dark_magician_lob_en005_reprint_trap():
    res = r(title="Dark Magician LOB-EN005 Ultra Rare", printed_set_code="LOB-EN005")
    assert res.status == IdentityStatus.CANDIDATES and len(res.candidates) == 2


def test_same_code_in_several_products_and_rarities():
    # LC01-EN004: Legendary Collection, its Gameboard Edition and the 25th Anniversary Edition (UR and QCSR).
    res = r(title="Blue-Eyes White Dragon LC01-EN004", printed_set_code="LC01-EN004")
    assert res.status == IdentityStatus.CANDIDATES and len(res.candidates) == 4
    assert {p.rarity for p in res.candidates} == {"Ultra Rare", "Quarter Century Secret Rare"}


def test_jmp_001_is_both_an_en_and_a_jp_promo():
    res = r(title="Blue-Eyes White Dragon JMP-001 promo", printed_set_code="JMP-001")
    assert res.status == IdentityStatus.CANDIDATES
    assert {p.region for p in res.candidates} == {Region.EN_TCG, Region.JP_OCG}
    jp = r(title="Blue-Eyes White Dragon JMP-001", printed_set_code="JMP-001", card_language="ja")
    assert jp.status == IdentityStatus.RESOLVED
    assert jp.printing.product_name.startswith("Weekly Shōnen Jump 2003")


def test_no_code_jp_secret_rare_promo_vs_box_prints():
    # V Jump Festa 1999 promo vs 25th Anniversary Ultimate Kaiba Set vs Original Artwork Collection: all code-less SR.
    res = r(title="青眼の白龍 シークレット", card_language="ja", verified_rarity="Secret Rare")
    names = {p.product_name for p in res.candidates if p.set_code is None}
    assert res.status == IdentityStatus.CANDIDATES
    assert {"V Jump Festa 1999 promotional card", "25th Anniversary Ultimate Kaiba Set",
            "Original Artwork Collection"} <= names


def test_ghost_vs_ultimate_vs_ultra_same_code():
    seller = r(title="Red Dragon Archfiend/Assault Mode CRMS-EN004 Ghost Rare", printed_set_code="CRMS-EN004",
               seller_rarity="Ghost Rare")
    assert seller.status == IdentityStatus.CANDIDATES
    assert {p.rarity for p in seller.candidates} == {"Ultra Rare", "Ultimate Rare", "Ghost Rare"}
    shown = r(title="Red Dragon Archfiend/Assault Mode", printed_set_code="CRMS-EN004", verified_rarity="Ghost Rare")
    assert [p.rarity for p in shown.candidates] == ["Ghost Rare"]
    assert shown.status == IdentityStatus.CANDIDATES   # EN edition still undocumented


def test_parallel_rare_shares_code_with_ultra_rare():
    res = r(title="青眼の白龍 DL2-001", printed_set_code="DL2-001", card_language="ja")
    assert {p.rarity for p in res.candidates} == {"Ultra Rare", "Ultra Parallel Rare"}
    ok = r(title="青眼の白龍 DL2-001", printed_set_code="DL2-001", card_language="ja",
           verified_rarity="Ultra Parallel Rare")
    assert ok.status == IdentityStatus.RESOLVED


def test_playable_card_jp_shared_code_rarities():
    res = r(title="灰流うらら QCDB-JP021", printed_set_code="QCDB-JP021")
    assert res.status == IdentityStatus.CANDIDATES
    assert {p.rarity for p in res.candidates} == {"Super Rare", "Secret Rare"}


def test_alternate_artwork_passcodes_share_family_but_not_artwork():
    res = r(title="Dark Magician RA04-EN106", printed_set_code="RA04-EN106", verified_rarity="Platinum Secret Rare")
    assert {p.family_id for p in res.candidates} == {"fam:k4041"}
    assert {p.artwork_id for p in res.candidates} == {"art:pw46986414", "art:pw36996508"}
    assert any("artwork" in e for e in res.evidence_needed)


def test_dark_magician_girl_title_does_not_match_dark_magician():
    res = r(title="Dark Magician Girl MFC-000 Secret Rare", printed_set_code="MFC-000")
    assert {p.family_id for p in res.candidates} == {"fam:k4766"}


def test_jp_card_language_contradicts_en_code():
    assert r(title="Blue-Eyes White Dragon", printed_set_code="LOB-EN001", card_language="ja").status \
        == IdentityStatus.CONFLICT


def test_seller_typo_with_old_en_code_needs_name_photo():
    res = r(title="Blue Eyes Whte Dragon SKE-001", printed_set_code="SKE-001")
    assert res.status == IdentityStatus.CANDIDATES
    assert [p.product_name for p in res.candidates] == ["Starter Deck: Kaiba Evolution"]


def test_wrong_printing_blocks_qualified():
    res = r(title="Blue-Eyes White Dragon LOB-EN001", printed_set_code="LOB-EN001")
    ev = evaluate(listing_ref="t", identity=res, n_comps=10, min_comps=3, ask_all_in=None,
                  ceiling=Decimal("100"), ask_price=Decimal("50"), listing_last_seen=NOW, now=NOW,
                  authenticity_ok=True)
    assert ev.stage != Stage.QUALIFIED
    assert any(r.startswith("identity") for r in ev.reasons)
