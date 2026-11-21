"""A character groups cards. It never gives them one price."""
from cardman.character import slug, species_of


def test_a_form_is_the_same_character_and_a_tag_team_is_both():
    assert species_of("Charizard ex") == ["Charizard"]
    assert species_of("Charizard VSTAR") == ["Charizard"]
    assert species_of("M Charizard-EX") == ["Charizard"]
    assert species_of("Radiant Charizard") == ["Charizard"]
    assert species_of("Darkrai") == ["Darkrai"]
    assert species_of("Charizard & Braixen-GX") == ["Charizard", "Braixen"]
    assert slug("Charizard") == "charizard"


def test_every_charizard_label_files_under_charizard():
    for name in ("Mega Charizard X ex", "Mega Charizard Y ex", "Charizard X", "Charizard G LV.X", "Charizard ★",
                 "Charizard ex -196", "Blaine's Charizard", "Lance's Charizard V", "Special Delivery Charizard",
                 "Shining Charizard", "Dark Charizard"):
        assert species_of(name) == ["Charizard"], name
    assert species_of("Hisuian Zoroark") == ["Zoroark"]
    assert species_of("N's Zoroark") == ["Zoroark"]


def test_trainer_cards_and_source_duplicates_are_not_characters_or_extra_cards(tmp_path):
    from cardman.character import species_index
    from tests.pkm_fixture import catalog
    cat = catalog(tmp_path)
    ix = species_index(cat)
    assert "Professor's Research" not in ix                       # a Trainer is not a Pokémon
    assert {cat._cards[c][1] for c in ix["Charizard"]} == {"Charizard", "Charizard-GX", "Charizard ex"}
    assert len(ix["Charizard"]) == 5                              # base1-4, sma-SV49, sv3pt5-6, sv3pt5-199, sv4pt5-234


def test_the_same_physical_card_from_two_sources_is_one_tile():
    import types
    from cardman.character import species_index
    rows = {"a": (None, "Charizard ex", "charizard ex", "sv3", "199", "Pokémon", None, None, None, "2023-09-22"),
            "b": (None, "Charizard EX", "charizard ex", "sv3", "199", "Pokémon", None, None, None, "2023-09-22")}
    cat = types.SimpleNamespace(_cards=rows, set=lambda sid: {"name": "151"},
                                versions=lambda cid: [types.SimpleNamespace(id=f"v:{cid}")] * (2 if cid == "b" else 1))
    assert species_index(cat)["Charizard"] == ["b"]


def _cat(cards, unpriced):
    import types
    def versions(cid):
        return [types.SimpleNamespace(id=f"v:{cid}")]
    def guide(pid):
        return None if pid == f"v:{unpriced}" else (12.5, "2026-11-01")
    return types.SimpleNamespace(_cards={cid: (None, name, None, None, None, None, None, None, None, rel)
                                         for cid, (name, rel) in cards.items()}, versions=versions, guide=guide)


def test_market_rows_rank_characters_and_open_on_cached_art(tmp_path):
    from cardman import cardinfo
    from cardman.character import market_rows
    cardinfo.IMAGES = tmp_path
    (tmp_path / "pkm" / "small").mkdir(parents=True)
    (tmp_path / "pkm" / "small" / "c2.png").write_bytes(b"\x89PNG")
    cat = _cat({"c1": ("Charizard ex", "2023-01-01"), "c2": ("Charizard VMAX", "2022-06-01"),
                "c3": ("Charizard", "2021-01-01"), "u1": ("Ununium", "2024-01-01")}, unpriced="u1")
    rows = market_rows(cat)
    assert [r[2] for r in rows] == ["Charizard"]                    # Ununium has no priced card: not a market yet
    priced, cards, _name, rep = rows[0]
    assert (priced, cards) == (3, 3) and rep == "c2"                # the tile opens on a card with cached art
