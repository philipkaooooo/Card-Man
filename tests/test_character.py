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
