"""A character is a Pokémon. A version's reading never becomes another version's price."""
from cardman.characters import book, trend


def test_the_icon_name_is_the_pokemon_not_the_print():
    who = book()
    assert [h[1] for h in who.of_card("Charizard ex")] == ["charizard"]
    assert [h[1] for h in who.of_card("M Charizard-EX")] == ["charizard"]
    assert [h[1] for h in who.of_card("Galarian Moltres")] == ["moltres"]
    assert [h[1] for h in who.of_card("Mr. Mime")] == ["mr-mime"]
    assert [h[1] for h in who.of_card("Farfetch'd")] == ["farfetchd"]
    assert [h[1] for h in who.of_card("Charizard & Braixen-GX")] == ["charizard", "braixen"]
    assert who.of_card("Professor's Research") == []


def test_one_reading_is_not_a_move_and_two_versions_do_not_share():
    cards = [{
        "name": "Charizard", "set": "Base", "number": "4", "versions": {
            "1st edition holo": [("2026-11-20", 1000.0)],
            "unlimited holo": [("2026-11-01", 400.0), ("2026-11-20", 450.0)],
        },
    }]
    t = trend(cards)
    assert t["unread"] == 1 and t["up"] == 1 and t["down"] == 0
    assert t["moves"][0]["edition"] == "unlimited holo"
    assert t["moves"][0]["start"] == 400.0 and t["moves"][0]["end"] == 450.0
    assert all(m["start"] != 1000.0 for m in t["moves"])
