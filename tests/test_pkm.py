"""Pokémon: catalog, versions, title reading on realistic listing titles, and the walls between the two games."""
import pytest

from cardman import pkm_intel
from cardman.games import card_href, card_key, game_of
from cardman.models import IdentityStatus
from tests.pkm_fixture import catalog


@pytest.fixture(scope="module")
def cat(tmp_path_factory):
    return catalog(tmp_path_factory.mktemp("pkm"))


def test_catalog_loads_sets_cards_and_real_versions(cat):
    assert len(cat) == 13
    ids = lambda cid: [p.edition for p in cat.versions(cid)]
    assert ids("base1-4") == ["1st Edition Holo", "Shadowless", "Unlimited Holo"]
    assert ids("base1-58") == ["1st Edition", "Shadowless", "Unlimited"]   # Base Set commons exist in all three too
    assert ids("sv3pt5-25") == ["Normal", "Reverse Holo"]
    assert ids("sv3pt5-199") == ["Holo"]
    assert ids("swshp-SWSH050") == ["Holo"]
    p = cat.versions("sv3pt5-199")[0]
    assert p.id == "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199" and p.set_code == "199/165" and p.product_name == "151"
    assert cat.guide(p.id) == (320.0, "2026-11-12")
    assert cat.guide("pk:EN:base1-4:unlimited-holo@pkm-base1-4") == (450.0, "2026-11-12")      # the one Base Set price is Unlimited
    assert cat.guide("pk:EN:base1-4:shadowless@pkm-base1-4") is None
    assert [h["card_id"] for h in cat.search("Base Set Charizard")] == ["base1-4"]


def test_ids_say_which_game_they_are():
    assert game_of("pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199") == "pkm" and game_of("pkm-sv3pt5-199") == "pkm"
    assert game_of("p:EN_TCG:LOB-EN001:legend:ultra-rare@89631139|ed:1st") == "ygo" and game_of("89631139") == "ygo"
    assert card_key("pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199") == "pkm-sv3pt5-199"
    assert card_key("p:EN_TCG:LOB-EN001:x:ultra-rare@89631139|ed:1st") == "89631139"
    assert card_href("pkm-sv3pt5-199") == "/pkm/card/sv3pt5-199" and card_href("89631139") == "/card/89631139"


def test_search_by_name_set_number_and_abbreviation(cat):
    first = lambda q: [h["card_id"] for h in cat.search(q)][:3]
    assert first("Charizard ex 151")[0] == "sv3pt5-199" or first("Charizard ex 151")[0] == "sv3pt5-6"
    assert first("199/165") == ["sv3pt5-199"]
    assert first("Charizard MEW 199")[0] == "sv3pt5-199"
    assert first("Umbreon VMAX Evolving Skies")[:2] in (["swsh7-215", "swsh7-95"], ["swsh7-95", "swsh7-215"])
    assert set(first("Paldean Fates Charizard")) == {"sv4pt5-234"}
    assert first("sv3pt5-199") == ["sv3pt5-199"]


def top(an):
    return max(an.weights, key=an.weights.get) if an.weights else None


def kinds(an):
    return {f.kind for f in an.findings}


CASES = [
    # title, expected top version id (or None), status / expected finding kinds, grade
    ("Charizard ex 199/165 SIR Pokemon 151 NM", "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "RESOLVED", None),
    ("Pokemon 151 Charizard ex Special Illustration Rare 199/165 PSA 10", "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "RESOLVED", ("PSA", "10")),
    ("Charizard ex #199 151 SIR", "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "RESOLVED", None),
    ("Charizard ex 234/091 Paldean Fates SIR", "pk:EN:sv4pt5-234:holo@pkm-sv4pt5-234", "RESOLVED", None),
    ("Pikachu 025/165 Reverse Holo 151", "pk:EN:sv3pt5-25:reverse-holo@pkm-sv3pt5-25", "CANDIDATES", None),
    ("Pikachu 025/165 151 Pokemon", "pk:EN:sv3pt5-25:normal@pkm-sv3pt5-25", "CANDIDATES", None),
    ("1999 Pokemon Base Set Charizard 4/102 Holo Rare 1st Edition", "pk:EN:base1-4:1st-edition-holo@pkm-base1-4", "CANDIDATES", None),
    ("Charizard 4/102 Shadowless Base Set Holo", "pk:EN:base1-4:shadowless@pkm-base1-4", "CANDIDATES", None),
    ("Base Set Charizard Unlimited Holo 4/102 LP", "pk:EN:base1-4:unlimited-holo@pkm-base1-4", "CANDIDATES", None),
    ("Umbreon VMAX 215/203 Alt Art Evolving Skies", "pk:EN:swsh7-215:holo@pkm-swsh7-215", "RESOLVED", None),
    ("Moonbreon Umbreon VMAX Alternate Art Secret Rare 215/203 CGC 9.5", "pk:EN:swsh7-215:holo@pkm-swsh7-215", "RESOLVED", ("CGC", "9.5")),
    ("Rayquaza VMAX 218/203 Evolving Skies alt art", "pk:EN:swsh7-218:holo@pkm-swsh7-218", "RESOLVED", None),
    ("Charizard GX SV49/SV94 Hidden Fates Shiny", "pk:EN:sma-SV49:holo@pkm-sma-SV49", "RESOLVED", None),
    ("Pikachu V SWSH050 Black Star Promo", "pk:EN:swshp-SWSH050:holo@pkm-swshp-SWSH050", "RESOLVED", None),
    ("Charizard ex 006/165 Double Rare 151", "pk:EN:sv3pt5-6:holo@pkm-sv3pt5-6", "RESOLVED", None),
    ("PSA 9 Pokemon Charizard Base Set 4/102 Holo", "pk:EN:base1-4:unlimited-holo@pkm-base1-4", "CANDIDATES", ("PSA", "9")),
    ("BGS 10 Pristine Charizard ex 199/165", "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "RESOLVED", ("BGS", "10")),
    ("Pokémon Pikachu 58/102 Base Set Red Cheeks", "pk:EN:base1-58:unlimited@pkm-base1-58", "CANDIDATES", None),
    ("Base Set Charizard 4/102 Holo Shadowless PSA 7", "pk:EN:base1-4:shadowless@pkm-base1-4", "CANDIDATES", ("PSA", "7")),
    ("Charizard ex 199/165 151 NM/M Pack Fresh", "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "RESOLVED", None),
    ("Bulbasaur 166/165 Illustration Rare 151 IR", "pk:EN:sv3pt5-166:holo@pkm-sv3pt5-166", "RESOLVED", None),
    ("Professor's Research 190/165 Full Art 151", "pk:EN:sv3pt5-190:holo@pkm-sv3pt5-190", "RESOLVED", None),
]


@pytest.mark.parametrize("title,want,status,grade", CASES)
def test_reads_real_looking_titles(cat, title, want, status, grade):
    an = pkm_intel.analyze(title, cat)
    assert top(an) == want, (title, an.weights)
    assert an.identity.status.value == status, (title, [c.id for c in an.identity.candidates])
    assert not an.blocked, (title, an.findings)
    assert (an.parsed.grade_company, an.parsed.grade) == (grade or (None, None))


BLOCKED = [
    ("Charizard ex 199/165 Japanese", "OUT_OF_SCOPE"),
    ("Charizard ex 199/165 German Deutsch", "OUT_OF_SCOPE"),
    ("Charizard ex 199/165 Korean", "OUT_OF_SCOPE"),
    ("Custom Charizard ex gold metal card", "COUNTERFEIT_CUE"),
    ("Charizard ex Proxy Card 199/165", "COUNTERFEIT_CUE"),
    ("Umbreon VMAX orica fan art card", "COUNTERFEIT_CUE"),
    ("Pokemon 151 Booster Box Sealed", "SEALED_PRODUCT"),
    ("Pokemon Scarlet Violet 151 Elite Trainer Box ETB", "SEALED_PRODUCT"),
    ("Pokemon 151 booster pack x1 factory sealed", "SEALED_PRODUCT"),
    ("Jumbo Charizard ex oversized promo", "NOT_A_CARD"),
    ("Pokemon TCG Live code card 151", "NOT_A_CARD"),
]


@pytest.mark.parametrize("title,kind", BLOCKED)
def test_fakes_sealed_jumbo_codes_and_other_languages_are_blocked(cat, title, kind):
    an = pkm_intel.analyze(title, cat)
    assert an.blocked and kind in kinds(an), (title, an.findings)


def test_lots_ambiguity_mismatch_and_stamps(cat):
    lot = pkm_intel.analyze("Pokemon card lot 100 cards bulk", cat)
    assert lot.parsed.is_lot and lot.parsed.lot_size == 100
    amb = pkm_intel.analyze("Charizard ex SIR", cat)
    assert amb.identity.status == IdentityStatus.CANDIDATES and {c.id for c in amb.identity.candidates} >= {
        "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "pk:EN:sv4pt5-234:holo@pkm-sv4pt5-234"}
    mm = pkm_intel.analyze("Pikachu 199/165 151", cat)
    assert "CODE_NAME_MISMATCH" in kinds(mm) and "Charizard ex" in next(f.text for f in mm.findings if f.kind == "CODE_NAME_MISMATCH")
    st = pkm_intel.analyze("Charizard ex 199/165 151 Prerelease stamp", cat)
    assert "STAMPED" in kinds(st) and not st.blocked
    two = pkm_intel.analyze("Umbreon VMAX Evolving Skies", cat)
    assert len({c.id for c in two.identity.candidates}) == 2
    assert "ex" not in (pkm_intel.parse("Charizard ex 199/165")["condition"] or "").lower()   # "ex" is a card name, not a condition


def test_unstated_reverse_holo_leans_to_the_normal_card(cat):
    w = pkm_intel.analyze("Pikachu 025/165 151", cat).weights
    assert w["pk:EN:sv3pt5-25:normal@pkm-sv3pt5-25"] > 2 * w["pk:EN:sv3pt5-25:reverse-holo@pkm-sv3pt5-25"]
    assert abs(sum(w.values()) - 1) < 1e-9


def test_imported_titles_go_to_the_right_game(cat):
    from cardman.games import guess_game
    assert guess_game("Pokemon Charizard ex 199/165 SIR", cat) == "pkm"
    assert guess_game("Charizard ex 199/165 SIR 151", cat) == "pkm"
    assert guess_game("Blue-Eyes White Dragon LOB-EN001 1st Edition", cat) == "ygo"
    assert guess_game("Yu-Gi-Oh! Dark Magician SDY-006", cat) == "ygo"
    assert guess_game("遊戯王 青眼の白龍", cat) == "ygo" and guess_game("ポケモンカード リザードン", cat) == "pkm"
    assert guess_game("Some card 12/34", None) == "ygo"


def test_a_pokemon_sale_never_prices_a_yugioh_card_and_back(tmp_path, cat):
    from datetime import datetime, timedelta, timezone
    from cardman.card_market import ensure_index, listings_for, summary
    from cardman.models import EvidenceType as ET
    from cardman.store import Store
    from tests.fixtures import obs
    from tests.test_card_market import _store
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    pk = "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199"
    yg = "p:EN_TCG:LOB-EN001:legend:ultra-rare@89631139"
    st = _store(tmp_path, [("a", "ebay_browse", [(pk, 1.0)], 300, {}), ("b", "ebay_browse", [(yg, 1.0)], 50, {})])
    ensure_index(st)
    assert [r["key"] for r in listings_for(st, "pkm-sv3pt5-199")] == ["a"]
    assert [r["key"] for r in listings_for(st, "89631139")] == ["b"]
    assert set(summary(st, ["pkm-sv3pt5-199", "89631139"])) == {"pkm-sv3pt5-199", "89631139"}
    for i in range(3):
        st.add_observation(obs(f"p{i}", ET.INDEPENDENT_TRANSACTION, "300", printing=pk, when=now - timedelta(days=i + 1),
                               synthetic_fixture=False), now=now)
    from cardman.evidence import select_comps
    assert select_comps(st.observations_for(yg, as_of=now), yg, as_of=now)[0] == []
    assert len(select_comps(st.observations_for(pk, as_of=now), pk, as_of=now)[0]) == 3


@pytest.fixture
def web(tmp_path, cat, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from cardman.cardinfo import CardIndex, ImageCache
    from cardman.pkm_index import PkmIndex
    from cardman.ui import make_handler
    from tests.real_catalog import DATA
    from tests.test_plain_words import _opps
    from tests.test_sold_import import CAT
    from cardman.store import Store
    monkeypatch.setattr("cardman.engine.ROOT", tmp_path)
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    _opps(st)
    import json
    d = {"title": "Charizard ex 199/165 SIR 151", "kinds": ["REFERENCE_GAP"], "game": "pkm", "all_in_home": 300.0,
         "scenarios": [{"printing_id": "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", "label": "151 199/165 · Special Illustration Rare · Holo",
                        "weight": 1.0, "basis": "reference", "resale_mid": 320.0, "resale_low": 192.0, "profit_mid": 1.0}]}
    with st.db:
        st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:pk1", "SPECTATE", "OPEN", 1.0, None, 0.0, "[]", None, None, "fp", json.dumps(d), "t", "t"))
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:pk1", "ebay_browse:EBAY_US", "pk1", "INTL", d["title"], "https://www.ebay.com/itm/2", None, "ask",
                       "280", "USD", None, "2026-11-10T00:00:00+00:00", "2026-11-12T00:00:00+00:00", None, "f", '{"game": "pkm"}'))
    st.db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    images = ImageCache(tmp_path / "img", get=lambda u: (b"\x89PNG fake", "image/png"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", catalog=lambda: CAT, cards=cards, images=images, pkm=PkmIndex(cat)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def get(path, cookie=None, follow=True):
        req = urllib.request.Request(base + path, headers={"Cookie": cookie} if cookie else {})
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
        try:
            r = opener.open(req)
            return r.status, r.geturl().replace(base, ""), dict(r.headers), r.read().decode(errors="replace")
        except urllib.error.HTTPError as x:
            return x.code, path, dict(x.headers), x.read().decode(errors="replace")
    yield get
    srv.shutdown()


def test_switch_routes_and_pages_for_pokemon(web):
    s, url, h, body = web("/pkm/")
    assert s == 200 and 'class="on">Pokémon<' in body and 'href="/pkm/opportunities"' in body and "199/165" in body
    assert "Charizard ex 199/165 SIR 151" in body and "Blue-Eyes White Dragon LOB-EN001 buy" not in body     # only Pokémon listings
    s, url, h, body = web("/")
    assert 'class="on">Yu-Gi-Oh!<' in body and "Charizard ex 199/165 SIR 151" not in body                    # and only Yu-Gi-Oh! here
    s, url, h, body = web("/game?g=pkm&back=/opportunities", follow=False)
    assert s == 303 and h["Location"] == "/pkm/opportunities" and "game=pkm" in h["Set-Cookie"]
    s, url, h, body = web("/game?g=ygo&back=/pkm/search%3Fq%3Dx", follow=False)
    assert h["Location"] == "/search?q=x" and "game=ygo" in h["Set-Cookie"]
    s, url, h, body = web("/", cookie="game=pkm", follow=False)
    assert s == 302 and h["Location"] == "/pkm/"
    s, url, h, body = web("/pkm/search?q=199/165")
    assert url == "/pkm/card/sv3pt5-199"
    assert all(x in body for x in ("Charizard ex", "330", "Version", "Price over time", "Regulation mark", "For sale now"))
    assert 'href="/pkm/search' in body or 'action="/pkm/search"' in body
    s, url, h, body = web("/pkm/search?q=Charizard")
    assert s == 200 and "/pkm/card/sv3pt5-199" in body and "/pkm/card/base1-4" in body
    s, url, h, body = web("/pkm/card/sv3pt5-199/history.json")
    assert s == 200 and '"currency"' in body
    s, url, h, body = web("/pkm/img/pkm/sv3pt5-199.png?size=small")
    assert s == 200 and h["Content-Type"] == "image/png"
    s, url, h, body = web("/pkm/opportunities?v=every")
    assert "Charizard ex 199/165 SIR 151" in body and "Blue-Eyes" not in body
    s, url, h, body = web("/opp/ebay_browse%3AEBAY_US%3Apk1")
    assert s == 200 and 'class="on">Pokémon<' in body and "/pkm/card/sv3pt5-199" in body


def test_pokemon_pages_speak_plainly(web):
    import re
    from tests.test_plain_words import BANNED, readable
    for p in ("/pkm/", "/pkm/opportunities?v=every", "/pkm/search?q=Charizard", "/pkm/card/sv3pt5-199", "/pkm/card/base1-4",
              "/opp/ebay_browse%3AEBAY_US%3Apk1"):
        text = readable(web(p)[3])
        found = [w for w in BANNED if re.search(re.escape(w), text, re.I)]
        assert not found, (p, found, [text[max(0, m.start() - 60):m.end() + 30] for w in found for m in [re.search(re.escape(w), text, re.I)]])


def test_reprints_and_restaurant_promos_never_pass_as_the_original(cat):
    re_ = pkm_intel.analyze("Pokemon TCG Charizard 4/102 30th Classic Collection", cat)
    assert "REPRINT" in kinds(re_) and not any(c.id.startswith("pk:EN:base1-4") for c in re_.identity.candidates)
    cel = pkm_intel.analyze("Charizard 4/102 Celebrations Classic Collection Holo", cat)
    assert not any("base1-4" in c.id for c in cel.identity.candidates)
    bk = pkm_intel.analyze("1999 BURGER KING POKEMON GOLD CARDS CHARIZARD PSA 9", cat)
    assert bk.blocked and "NOT_A_CARD" in kinds(bk)
    assert top(pkm_intel.analyze("1999 POKEMON BASE SET UNLIMITED #4 CHARIZARD-HOLO PSA 1", cat)) == "pk:EN:base1-4:unlimited-holo@pkm-base1-4"


def test_versions_by_era_when_the_source_lists_none():
    from cardman.pkm_catalog import _inferred_versions as inf
    c = lambda sid, rar, series="Scarlet & Violet": {"set": {"id": sid, "series": series}, "rarity": rar}
    assert inf(c("neo1", "Rare Holo", "Neo")) == ["1st Edition Holo", "Unlimited Holo"]
    assert inf(c("base2", "Common", "Base")) == ["1st Edition", "Unlimited"]
    assert inf(c("base4", "Rare Holo", "Base")) == ["Holo"]
    assert inf(c("sv3pt5", "Common")) == ["Normal", "Reverse Holo"]
    assert inf(c("swsh7", "Rare Holo", "Sword & Shield")) == ["Holo", "Reverse Holo"]
    assert inf(c("sv3pt5", "Special Illustration Rare")) == ["Holo"]
    assert inf(c("swshp", "Promo", "Promo")) == ["Promo"]


def test_alt_art_cards_are_real_and_custom_art_cards_are_not(cat):
    ok = pkm_intel.analyze("2021 SWSH Pokemon Evolving Skies Umbreon VMAX Alt Art Card 215/203 NM", cat)
    assert not ok.blocked and top(ok) == "pk:EN:swsh7-215:holo@pkm-swsh7-215"
    assert not pkm_intel.analyze("Umbreon VMAX 215/203 Full Art Card", cat).blocked
    assert "COUNTERFEIT_CUE" in kinds(pkm_intel.analyze("Lucario EX Pokemon Card FULL ART Display Art Card", cat))


def test_a_bare_hash_number_does_not_beat_the_name(cat):
    an = pkm_intel.analyze("Pokemon Pikachu V BLACK STAR PROMO SWSH #050 PSA 10", cat)
    assert top(an) == "pk:EN:swshp-SWSH050:holo@pkm-swshp-SWSH050" and "CODE_NAME_MISMATCH" not in kinds(an)
    an = pkm_intel.analyze("Umbreon VMAX #4 Evolving Skies", cat)
    assert {c.id.split(":")[2] for c in an.identity.candidates} == {"swsh7-215", "swsh7-95"} and "CODE_NAME_MISMATCH" not in kinds(an)


def test_names_sellers_shorten_or_reorder_still_confirm_the_number(cat):
    # sellers drop "ex", put the form name after, or use a curly apostrophe
    assert "CODE_NAME_MISMATCH" not in kinds(pkm_intel.analyze("Charizard 199/165 SIR 151", cat))
    assert "CODE_NAME_MISMATCH" not in kinds(pkm_intel.analyze("Research Professor’s 190/165 151", cat))
    from cardman.pkm_catalog import versions_of
    assert versions_of({"set": {"id": "neo1"}, "rarity": "Rare Holo", "tcgplayer": {"prices": {"unlimitedHolofoil": {}}}}, {}) == \
        ["1st Edition Holo", "Unlimited Holo"]


def test_more_languages_and_a_person_s_reading_of_the_number(cat):
    assert "OUT_OF_SCOPE" in kinds(pkm_intel.analyze("Pokemon Card Indonesia Charizard ex 199/165 SAR", cat))
    assert "OUT_OF_SCOPE" in kinds(pkm_intel.analyze("Pokemon S-Chinese Umbreon VMAX 215/203", cat))
    an = pkm_intel.analyze("Pikachu pokemon card 151", cat, verified={"printed_set_code": "025/165", "verified_edition": "Reverse Holo"})
    assert [c.id for c in an.identity.candidates] == ["pk:EN:sv3pt5-25:reverse-holo@pkm-sv3pt5-25"]
