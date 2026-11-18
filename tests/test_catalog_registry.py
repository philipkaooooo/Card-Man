"""The master card list: exact matching rules, reprints kept apart from originals, and the completeness check.

Uses a tiny hand-made catalog and a recorded TCGplayer snapshot (written to a temp folder), never the network.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from cardman.catalog import registry as R
from cardman.identity import Catalog
from cardman.models import CardFamily, Printing, Region

DM = CardFamily("fam:dm", {"en": "Dark Magician"}, passcode="46986414")
BABY = CardFamily("fam:baby", {"en": "Babycerasaurus"}, passcode="1")
LOB = "Legend of Blue Eyes White Dragon"


def P(pid, fam, art, code, product, rarity, day):
    return Printing(pid, fam, art, Region.EN_TCG, "en", product, rarity, set_code=code, release_date=day, product_name=product)


ORIG = P("p:LOB-005:lob:ur@46986414", "fam:dm", "art:dm", "LOB-005", LOB, "Ultra Rare", "2002-03-08")
ANNIV = P("p:LOB-EN005:lob25:ur@46986414", "fam:dm", "art:dm", "LOB-EN005", f"{LOB} (25th Anniversary Edition)", "Ultra Rare", "2023-04-20")
EU = P("p:LOB-EN005:lob:ur@46986414", "fam:dm", "art:dm", "LOB-EN005", LOB, "Ultra Rare", "2002-03-08")
RETRO = P("p:RP01-EN001:rp:r@46986414", "fam:dm", "art:dm2", "RP01-EN001", "Retro Pack", "Rare", "2009-01-01")
DL = P("p:DL13-EN012:dl:r@1", "fam:baby", "art:baby", "DL13-EN012", "Duelist League 13", "Rare", "2012-01-01")
YCAT = Catalog([DM, BABY], [ORIG, ANNIV, EU, RETRO, DL])


def prod(pid, name, num, rarity):
    return {"productId": pid, "name": name, "extendedData": [{"name": "Number", "value": num}, {"name": "Rarity", "value": rarity}]}


SNAPSHOT = {  # groupId -> (group name, products, finishes)
    1: (f"{LOB} (25th Anniversary Edition)", [prod(101, "Dark Magician", "LOB-EN005", "Ultra Rare")], ["Unlimited"]),
    2: (LOB, [prod(102, "Dark Magician", "LOB-005", "Ultra Rare")], ["1st Edition"]),
    3: ("Retro Pack", [prod(103, "Dark Magician", "RP01-EN001", "Rare")], ["Unlimited"]),
    4: ("Retro Pack (2020 Date Reprint)", [prod(104, "Dark Magician", "RP01-EN001", "Rare")], ["Unlimited"]),
    5: ("Duelist League 13", [prod(105, "Babycerasaurus (Red)", "DL13-EN012", "Rare"),
                              prod(106, "Babycerasaurus (Blue)", "DL13-EN012", "Rare")], ["Unlimited"]),
    6: ("Brand New Set", [prod(107, "Brand New Card", "BNS-EN001", "Secret Rare")], ["1st Edition"]),
}


def snapshot(root: Path) -> Path:
    folder = root / "2026-11-17" / "2"
    folder.mkdir(parents=True)
    (folder / "groups.json").write_text(json.dumps({"results": [{"groupId": g, "name": n, "abbreviation": "", "publishedOn": "2026-01-01"}
                                                                 for g, (n, _, _) in SNAPSHOT.items()]}))
    for g, (_, prods, fins) in SNAPSHOT.items():
        (folder / f"{g}.products.json").write_text(json.dumps({"results": prods}))
        (folder / f"{g}.prices.json").write_text(json.dumps({"results": [
            {"productId": p["productId"], "subTypeName": f, "lowPrice": 1.0, "midPrice": 2.0, "marketPrice": float(p["productId"])}
            for p in prods for f in fins]}))
    return folder


@pytest.fixture
def built(tmp_path):
    db = tmp_path / "catalog.sqlite"
    tcg = snapshot(tmp_path / "tcgcsv")
    R.build(db, ygo=YCAT, tcg_ygo=tcg, log=lambda *_: None, setlists=tcg / "none")
    return sqlite3.connect(db), db, tcg


def linked(con, product_id):
    return [r[0] for r in con.execute("SELECT pid FROM xref WHERE source='tcgplayer' AND ext_id=?", (str(product_id),))]


def test_reprint_and_original_never_share_a_tcgplayer_product(built):
    con, _, _ = built
    assert linked(con, 101) == [ANNIV.id]                         # 25th Anniversary product -> the 25th printing only
    assert linked(con, 102) == [ORIG.id]
    row = con.execute("SELECT is_reprint, reprint_of FROM printing WHERE pid=?", (ANNIV.id,)).fetchone()
    assert row == (1, ORIG.id)                                    # same artwork, printed later: a reprint of the 2002 card


def test_a_reprint_wave_is_its_own_version_linked_to_the_original(built):
    con, _, _ = built
    assert linked(con, 103) == [RETRO.id]
    sub = f"{RETRO.id}|v:2020 Date Reprint"
    assert linked(con, 104) == [sub]
    assert con.execute("SELECT is_reprint, reprint_of FROM printing WHERE pid=?", (sub,)).fetchone() == (1, RETRO.id)


def test_versions_tcgplayer_sells_apart_stay_apart_with_their_own_prices(built):
    con, _, _ = built
    red, blue = linked(con, 105), linked(con, 106)
    assert red != blue and red[0].endswith("|v:Red") and blue[0].endswith("|v:Blue")
    # every version carries only its own product's price (the fixture's market price is the product id)
    for pid, market in con.execute("SELECT pid, market FROM market_price"):
        assert [str(int(market))] == [r[0] for r in con.execute("SELECT ext_id FROM xref WHERE pid=?", (pid,))]


def test_a_card_our_list_lacks_is_added_not_dropped(built):
    con, _, _ = built
    assert linked(con, 107) == ["tcg-107"]
    assert con.execute("SELECT name, number, sources FROM printing WHERE pid='tcg-107'").fetchone() == ("Brand New Card", "BNS-EN001", "tcgplayer")


def test_zero_missing_against_the_recorded_snapshot_and_rebuilds_are_stable(built):
    con, db, tcg = built
    assert R.missing(con) == {}                                   # every product placed: nothing waits for a look
    n = con.execute("SELECT COUNT(*) FROM xref").fetchone()[0]
    R.build(db, ygo=YCAT, tcg_ygo=tcg, log=lambda *_: None, setlists=tcg / "none")
    assert con.execute("SELECT COUNT(*) FROM xref").fetchone()[0] == n   # no links left over from an earlier build


def test_ambiguous_products_wait_for_a_look_instead_of_being_guessed(tmp_path):
    two = Catalog([DM], [ORIG, P("p:LOB-005:other:ur@46986414", "fam:dm", "art:dm", "LOB-005", "Some Other Box", "Ultra Rare", "2003-01-01")])
    db, tcg = tmp_path / "c.sqlite", snapshot(tmp_path / "t")
    R.build(db, ygo=two, tcg_ygo=tcg, log=lambda *_: None, setlists=tcg / "none")
    con = sqlite3.connect(db)
    assert linked(con, 102) == [ORIG.id]                          # the set name decides between our two sets
    assert R.missing(con) == {}


def test_matching_rules():
    assert R._split_name("Murkrow - 021 (Prerelease) [Staff]") == ("Murkrow", "Prerelease Staff")
    assert R._ygo_rarity("Prismatic Collector's Rare") == {R.norm("Collector's Rare")}
    assert R.norm("Short Print") in R._ygo_rarity("Common")
    assert R._ygo_subversion("Blue-Eyes White Dragon (8th Art) (Quarter Century Secret Rare)", "RA04-EN108", "Quarter Century Secret Rare") == "8th Art"
    assert R._ygo_subversion("Artifact Lancea (PCR)", "RA01-EN006", "Prismatic Collector's Rare") == ""
    assert R._ygo_subversion("Exosister Elis (Emblazoned Secret Rare)", "26LP-EN016", "Secret Rare") == "Emblazoned Secret Rare"
    assert R._reprint_group("Retro Pack (2020 Date Reprint)") == "2020 Date Reprint"
    assert R._reprint_group("Retro Pack (2020 Date Reprint)", strip=True) == "Retro Pack"
    # a promo, energy or trainer-kit group is never the main set it is named after
    assert R._kind_words("ME: Mega Evolution Promo") != R._kind_words("Mega Evolution")
    assert R._kind_words("BW Trainer Kit: Excadrill & Zoroark") != R._kind_words("Black & White")
    assert R._kind_words("SV07: Stellar Crown") == R._kind_words("Stellar Crown")


def test_setlist_gaps_count_only_physical_sets_we_lack(tmp_path):
    db = tmp_path / "c.sqlite"
    b = R.Builder(db)
    b._write([["pk:EN:base1-4:x", "pkm", "base1", "Base", "1999-01-09", "4/102", "Charizard", "Rare Holo", "Unlimited Holo", "en",
               None, 0, None, ["pokemontcg.io"]]], "pkm")
    root = tmp_path / "lists"
    root.mkdir()
    sets = [{"id": "base1", "name": "Base Set", "cardCount": {"total": 102}},
            {"id": "A1", "name": "Genetic Apex", "cardCount": {"total": 286}},          # Pocket: a phone game, out of scope
            {"id": "wp0", "name": "Empty Set", "cardCount": {"total": 0}}]
    (root / "tcgdex_en_sets.json").write_text(json.dumps(sets))
    assert R.setlist_gaps(b.con, root) == []
    (root / "tcgdex_en_sets.json").write_text(json.dumps(sets + [{"id": "zz9", "name": "Unknown Expansion", "cardCount": {"total": 50}}]))
    assert [g[2] for g in R.setlist_gaps(b.con, root)] == ["zz9"]


REAL = R.DB.exists() and (R.ROOT / "data" / "cache" / "pkm.sqlite").exists()


@pytest.mark.skipif(not REAL, reason="needs the local master card list")
def test_real_list_has_every_30th_celebration_card_and_the_celebi_reprint():
    con = sqlite3.connect(R.DB)
    main = con.execute("SELECT COUNT(DISTINCT substr(pid, 1, instr(pid, '@'))) FROM printing WHERE set_id='me55'").fetchone()[0]
    classic = {n for (n,) in con.execute("SELECT name FROM printing WHERE set_id='me55c'")}
    assert main >= 161 and len(classic) >= 29                     # 161 main cards; 30 Classic Collection (two Darkrai halves share a name)
    celebi = con.execute("SELECT is_reprint, reprint_of FROM printing WHERE pid LIKE 'pk:EN:me55c-106:%'").fetchone()
    assert celebi[0] == 1 and celebi[1].startswith("neo4-106")   # Shining Celebi 106/105 is a reprint of Neo Destiny's


@pytest.mark.skipif(not REAL, reason="needs the local master card list")
def test_real_celebi_listing_matches_the_30th_reprint_not_the_neo_original():
    from cardman.pkm_catalog import PkmCatalog
    from cardman.pkm_intel import analyze
    cat = PkmCatalog()
    a = analyze("Pokemon 30th Celebration Classic Collection Shining Celebi 106/105 Holo NM", cat)
    assert a.weights and all(c.split(":")[2] == "me55c-106" for c in a.weights)
    b = analyze("Pokemon Shining Celebi Neo Destiny 106/105 1st Edition Holo", cat)
    assert b.weights and all(c.split(":")[2] == "neo4-106" for c in b.weights)
