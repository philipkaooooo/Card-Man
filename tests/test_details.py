"""A listing's own photos and eBay's item details: storage, serving, and what the details do to identity."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cardman import aspect_identity, listing_detail as ld
from cardman.market_sources import RawListing

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def item(iid="123456789012", aspects=None, images=3):
    return {"legacyItemId": iid, "title": "x",
            "image": {"imageUrl": f"https://i.ebayimg.com/images/g/AAA{iid}/s-l500.jpg"},
            "additionalImages": [{"imageUrl": f"https://i.ebayimg.com/images/g/B{i}{iid}/s-l1600.jpg"} for i in range(images - 1)]
                                + [{"imageUrl": "https://evil.example/x.jpg"}],
            "localizedAspects": [{"name": k, "value": v} for k, v in (aspects or {}).items()] + [{"name": "Seller Notes", "value": "hi"}]}


def test_photos_and_details_are_kept_per_item_and_only_from_ebay_hosts(tmp_path):
    db = sqlite3.connect(tmp_path / "s.sqlite")
    ld.save(db, "123456789012", item(aspects={"Card Number": "LOB-EN001", "Rarity": "Ultra Rare"}), NOW, "item")
    got = ld.get(db, "123456789012")
    assert len(got["images"]) == 3 and all(u.startswith("https://i.ebayimg.com/") and u.endswith("s-l1600.jpg") for u in got["images"])
    assert got["aspects"] == {"Card Number": "LOB-EN001", "Rarity": "Ultra Rare"}          # only identity fields are kept
    ld.save(db, "123456789012", {"image": {"imageUrl": "https://i.ebayimg.com/images/g/AAA123456789012/s-l225.jpg"}}, NOW, "search")
    assert ld.get(db, "123456789012")["aspects"]["Card Number"] == "LOB-EN001" and len(ld.get(db, "123456789012")["images"]) == 3


def test_photo_cache_keys_size_cap_and_eviction(tmp_path):
    db = sqlite3.connect(tmp_path / "s.sqlite")
    db.execute("CREATE TABLE listing (listing_id TEXT, ended_at TEXT)")
    ld.save(db, "123456789012", item(), NOW, "item")
    calls = []

    def fetch(url):
        calls.append(url)
        return (b"\xff\xd8" + b"x" * 100, "image/jpeg") if "B0" in url or "AAA" in url else (b"x" * (ld.MAX_BYTES + 10), "image/jpeg")
    body, ctype = ld.photo(db, "123456789012", 0, "m", root=tmp_path / "c", fetch=fetch)
    assert ctype == "image/jpeg" and calls[0].endswith("/s-l500.jpg")
    assert ld.photo(db, "123456789012", 0, "m", root=tmp_path / "c", fetch=fetch)[0] == body and len(calls) == 1     # cached
    assert ld.photo(db, "123456789012", 2, "m", root=tmp_path / "c", fetch=fetch) is None                              # too big
    assert ld.photo(db, "123456789012", 9, "m", root=tmp_path / "c", fetch=fetch) is None                              # no such photo
    assert ld.photo(db, "../etc", 0, "m", root=tmp_path / "c", fetch=fetch) is None
    k = ld.cache_key("123456789012", 0, "m")
    assert k != ld.cache_key("123456789012", 0, "l") and (tmp_path / "c" / k[:2] / f"{k}.img").exists()
    db.execute("INSERT INTO listing VALUES ('123456789012', ?)", ((NOW - timedelta(days=31)).isoformat(),))
    assert ld.evict(db, NOW, root=tmp_path / "c") >= 1 and not (tmp_path / "c" / k[:2] / f"{k}.img").exists()


def test_details_signals():
    s = ld.signals({"Game": "Pokémon TCG", "Card Name": "Charizard ex", "Card Number": "199/165", "Features": "Reverse Holo",
                    "Graded": "Yes", "Professional Grader": "Professional Sports Authenticator (PSA)", "Grade": "10", "Language": "English"})
    s.pop("_features", None)
    assert s == {"game": "pkm", "name": "Charizard ex", "number": "199/165", "finish": "Reverse Holo", "language": "English",
                 "grader": "PSA", "grade": "10"}
    assert ld.signals({"Features": "1st Edition, Holo", "Game": "Yu-Gi-Oh! TCG"})["edition"] == "1st Edition"
    assert "grade" not in ld.signals({"Graded": "No", "Grade": "10"})


@pytest.fixture(scope="module")
def ycat():
    from tests.test_engine import CAT
    return CAT


def test_ygo_details_agreeing_with_the_title_settle_the_printing(ycat):
    h = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001 Ultra Rare 1st Edition",
                            {"game": "ygo", "number": "LOB-EN001", "rarity": "Ultra Rare", "edition": "1st Edition"}, ycat)
    assert h.conflict is None and h.verified == {"printed_set_code": "LOB-EN001", "verified_rarity": "Ultra Rare", "verified_edition": "1st",
                                                 "verified_product": "Legend of Blue Eyes White Dragon"}
    from cardman.intel import analyze
    an = analyze("Blue-Eyes White Dragon LOB-EN001 Ultra Rare 1st Edition", ycat, verified=h.verified)
    assert an.identity.printing is not None and an.identity.printing.id.endswith("|ed:1st")


def test_ygo_details_that_contradict_the_title_are_a_reason_to_check(ycat):
    # details copied from another listing, while the title agrees with itself: go by the title, no alarm
    h = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001", {"number": "SDK-001"}, ycat)
    assert h.conflict is None and h.ignored and not h.verified
    # the title names one card and its code belongs to another: the details cannot settle it
    h = aspect_identity.ygo("Dark Magician LOB-EN001", {"number": "SDK-001"}, ycat)
    assert h.conflict and "LOB-EN001" in h.conflict
    h = aspect_identity.ygo("Dark Magician Ultra Rare", {"number": "LOB-EN001", "name": "Blue-Eyes White Dragon"}, ycat)
    assert h.conflict and "another card" in h.conflict
    h = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001 1st Edition", {"number": "LOB-EN001", "edition": "Unlimited"}, ycat)
    assert h.conflict and "edition" in h.conflict
    # a rarity spelled another way is simply not used
    h = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001", {"number": "LOB-EN001", "rarity": "Utra Rare"}, ycat)
    assert h.conflict is None and "verified_rarity" in h.verified       # LOB-EN001 has one rarity anyway
    assert aspect_identity.ygo("Charizard ex", {"game": "pkm"}, ycat).other_game == "pkm"


@pytest.fixture(scope="module")
def pcat(tmp_path_factory):
    from tests.pkm_fixture import catalog
    return catalog(tmp_path_factory.mktemp("pkmd"))


def test_pkm_details_settle_the_version_and_default_to_the_common_one(pcat):
    from cardman.pkm_intel import analyze
    h = aspect_identity.pkm("Pikachu 151 pokemon card", {"game": "pkm", "number": "025/165", "set": "SV: Scarlet & Violet 151",
                                                          "finish": "Reverse Holo"}, pcat)
    an = analyze("Pikachu 151 pokemon card", pcat, verified={**h.verified, "from_details": True})
    assert [c.id for c in an.identity.candidates] == ["pk:EN:sv3pt5-25:reverse-holo@pkm-sv3pt5-25"]
    h = aspect_identity.pkm("Pikachu 025/165 151", {"number": "025/165", "name": "Pikachu"}, pcat)
    an = analyze("Pikachu 025/165 151", pcat, verified={**h.verified, "from_details": True})
    assert [c.id for c in an.identity.candidates] == ["pk:EN:sv3pt5-25:normal@pkm-sv3pt5-25"]      # reverse holo is always advertised
    h = aspect_identity.pkm("Charizard 4/102 Base Set Holo", {"number": "4/102", "name": "Charizard"}, pcat)
    an = analyze("Charizard 4/102 Base Set Holo", pcat, verified={**h.verified, "from_details": True})
    assert [c.id for c in an.identity.candidates] == ["pk:EN:base1-4:unlimited-holo@pkm-base1-4"]


def test_pkm_details_that_contradict_the_title(pcat):
    h = aspect_identity.pkm("Charizard ex 199/165 151", {"number": "006/165"}, pcat)
    assert h.conflict is None and h.ignored                                  # the title's number and name agree
    h = aspect_identity.pkm("Pikachu 199/165 151", {"number": "025/165"}, pcat)
    assert h.conflict and "025/165" in h.conflict                           # the title disagrees with itself
    h = aspect_identity.pkm("Charizard ex SIR 151", {"name": "Umbreon VMAX"}, pcat)
    assert h.conflict and "Umbreon VMAX" in h.conflict
    h = aspect_identity.pkm("Raichu Base Set Shadowless Holo 4/102 Charizard", {"edition": "Unlimited"}, pcat)
    assert h.conflict is None and h.verified.get("verified_edition") == "Shadowless"
    assert aspect_identity.pkm("Blue-Eyes", {"game": "ygo"}, pcat).other_game == "ygo"
    assert aspect_identity.pkm("Charizard ex 199/165", {"language": "Japanese"}, pcat).language == "japanese"
    assert "mewtwo" in aspect_identity._words("#151MEWTWO EX")


def test_engine_uses_details_and_puts_graded_listings_in_the_graded_pool(tmp_path):
    from tests.test_engine import make
    e = make(tmp_path)
    ld.save(e.db, "998877665544", item("998877665544", {"Game": "Yu-Gi-Oh! TCG", "Card Number": "LOB-EN001", "Rarity": "Ultra Rare",
                                                         "Features": "1st Edition", "Graded": "Yes", "Professional Grader": "PSA",
                                                         "Grade": "9"}), NOW, "item")
    r = RawListing(source="ebay_browse:EBAY_US", listing_id="998877665544", title="Blue-Eyes White Dragon LOB-EN001 1st Ed",
                   price=__import__("decimal").Decimal("500"), currency="USD")
    an = e._analyze(r, {})
    assert an.identity.printing is not None and an.has("ASPECTS_CONFIRM")
    assert (r.grade_company, r.grade) == ("PSA", "9")
    r2 = RawListing(source="ebay_browse:EBAY_US", listing_id="111", title="Blue-Eyes White Dragon LOB-EN001 PSA 10",
                    price=__import__("decimal").Decimal("500"), currency="USD")
    e._analyze(r2, {})
    assert (r2.grade_company, r2.grade) == ("PSA", "10")


def test_a_shared_code_picks_the_reprint_only_when_it_is_said(ycat):
    plain = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001 1st Edition", {"number": "LOB-EN001", "rarity": "Ultra Rare"}, ycat)
    assert plain.verified["verified_product"] == "Legend of Blue Eyes White Dragon"
    rep = aspect_identity.ygo("Blue-Eyes White Dragon LOB-EN001 25th Anniversary", {"number": "LOB-EN001", "rarity": "Ultra Rare",
                                                                                     "set": "Legend of Blue Eyes White Dragon 25th Anniversary"}, ycat)
    assert rep.verified == {}          # anniversary / reprint wording: eBay's details never settle it (title evidence only)


def test_special_prints_reprints_and_lots_are_never_settled_by_the_details(ycat, pcat):
    for t in ("Blue-Eyes White Dragon LOB-001 Reprint 2010", "Hyozanryu MRL-075 1st Edition Asian English", "Summoned Skull MRD-003 LOB-122 lot"):
        assert not aspect_identity.ygo(t, {"number": "LOB-001" if "LOB-001" in t else "MRL-075" if "MRL" in t else "MRD-003"}, ycat).verified, t
    for t, sig in (("Charizard ex 199/165 151 Prerelease", {"number": "199/165"}),
                   ("Charizard 4/102 Base Set ERROR No Holo", {"number": "4/102"}),
                   ("Charizard ex 199/165 151", {"number": "199/165", "edition": "1st Edition Prerelease"})):
        assert not aspect_identity.pkm(t, sig, pcat).verified, t
    from cardman.pkm_intel import analyze
    an = analyze("Charizard ex 199/165 151 Prerelease stamp", pcat)
    assert an.identity.printing is None                       # a stamped card is never taken for the normal one


def test_several_numbers_are_several_cards_and_a_bare_number_finds_the_promo(pcat):
    from cardman.pkm_intel import analyze, parse
    assert parse("Ivysaur 30/102 + Wartortle 42/102 + Charmeleon 24/102 Base Set")["lot"]
    an = analyze("Pikachu V 151 Promo", pcat, verified={"printed_set_code": "SWSH050", "set_hint": "151", "from_details": True})
    assert [c.id for c in an.identity.candidates] == ["pk:EN:swshp-SWSH050:holo@pkm-swshp-SWSH050"]
    an = analyze("Pikachu 151", pcat, verified={"printed_set_code": "999", "from_details": True})
    assert an.identity.printing is None                       # a number that matches nothing settles nothing


def test_a_chase_rarity_on_a_plain_card_is_left_for_a_person(pcat):
    from cardman.pkm_intel import analyze
    an = analyze("Pikachu 151", pcat, verified={"printed_set_code": "025/165", "rarity_hint": "Ultra Rare", "from_details": True})
    assert an.identity.printing is None
    an = analyze("Charizard ex 151", pcat, verified={"printed_set_code": "199/165", "rarity_hint": "Special Illustration Rare", "from_details": True})
    assert an.identity.printing is not None


def test_no_false_alarms_for_titles_that_name_no_card_or_spell_a_number_differently(pcat):
    assert aspect_identity.pkm("1st Edition - Old Pokemon Cards - 100% Vintage", {"name": "Charizard"}, pcat).conflict is None
    assert aspect_identity.pkm("Charzard sir 151", {"name": "Charizard ex", "number": "199/165"}, pcat).conflict is None
    assert aspect_identity.pkm("Pikachu V SWSH050 Black Star Promo", {"number": "050"}, pcat).conflict is None
