"""The card page's PriceCharting section: one product per card, whatever printing the page opened on."""
import json
from datetime import datetime, timezone

from cardman import pc, ui_pc
from cardman.store import Store

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
CARD = "pk:EN:base1-4:unlimited-holo@pkm-base1-4"
FIRST_ED = "pk:EN:base1-4:1st-edition-holo@pkm-base1-4"


def _store(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    st.db.executescript(pc.SCHEMA)
    with st.db:
        st.db.execute(
            "INSERT INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, pop, fetched, "
            "status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (CARD, "pkm", "https://www.pricecharting.com/game/pokemon-base-set/charizard-4", "Charizard #4",
             "q", None, json.dumps({"Ungraded": {"p": 120.0, "c": 1.0, "vol": None}}),
             json.dumps({"Ungraded": [[1793512800000, 100.0], [1796191200000, 120.0]]}), "{}",
             NOW.isoformat(), "ok"))
    return st


def test_a_premium_printing_page_falls_back_to_the_card_product(tmp_path, monkeypatch):
    st = _store(tmp_path)
    monkeypatch.setattr(pc, "canonical_for", lambda pid: None if pid == CARD else CARD)
    h = ui_pc.section(st, FIRST_ED, "USD", NOW)
    assert 'id="pcsheet"' in h and "$120.00" in h
    assert "this card at each grade" in h


def test_no_row_anywhere_draws_nothing(tmp_path, monkeypatch):
    st = _store(tmp_path)
    monkeypatch.setattr(pc, "canonical_for", lambda pid: None)
    assert ui_pc.section(st, FIRST_ED, "USD", NOW) == ""
