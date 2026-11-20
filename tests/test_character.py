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
