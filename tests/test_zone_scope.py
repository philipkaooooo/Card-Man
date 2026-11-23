"""Characters page: Gen I scope, printing coverage, honest 12-month moves, corrected prices."""
import json
from datetime import datetime, timezone

from cardman import pc, zone
from cardman.store import Store

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
ZARD = "pk:EN:base1-4:unlimited-holo@pkm-base1-4"


def ms(days_ago: float) -> int:
    return int((NOW.timestamp() - days_ago * 86400) * 1000)


class Pr:
    def __init__(self, pid):
        self.id = pid


class Cat:
    def __init__(self):
        self._cards = {
            "zard": ("zard", "Charizard", "charizard", "base1", "4", "Pokémon", "Rare", None, None, "1999-01-09"),
            "chik": ("chik", "Chikorita", "chikorita", "neo1", "1", "Pokémon", "Rare", None, None, "2000-12-16"),
        }
        self.by_card = {
            "zard": [Pr(ZARD), Pr("pk:EN:base1-4:unlimited-holo@pkm-base1-4")],
            "chik": [Pr("pk:EN:neo1-1:holo@pkm-neo1-1")],
        }

    def versions(self, cid):
        return self.by_card[cid]


def seed(tmp_path, series=None, grades=None, extra=None):
    st = Store(tmp_path / "e.sqlite")
    st.db.executescript(pc.SCHEMA)
    rows = [(ZARD, series, grades)] + list(extra or [])
    with st.db:
        for pid, ser, gr in rows:
            st.db.execute(
                "INSERT INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, pop, fetched, "
                "status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, "pkm", None, None, "q", None, json.dumps(gr or {}), json.dumps(ser or {}), "{}",
                 NOW.isoformat(), "ok"))
    return st


def full_series(last=1606.4, first=1400.0):
    return {"Ungraded": [[ms(400), first], [ms(200), (first + last) / 2], [ms(6), last]]}


def test_a_move_is_only_claimed_with_a_full_year_of_history():
    last, move, fresh = zone._week([[ms(400), 80.0], [ms(380), 100.0], [ms(6), 150.0]], NOW)
    assert last == 150.0 and round(move, 1) == 50.0 and fresh
    last, move, fresh = zone._week([[ms(180), 100.0], [ms(6), 150.0]], NOW)
    assert last == 150.0 and move is None and fresh


def test_a_stale_capture_does_not_vote_on_the_move():
    last, move, fresh = zone._week([[ms(400), 80.0], [ms(60), 100.0]], NOW)
    assert last == 100.0 and round(move, 1) == 25.0 and fresh is False


def test_cap_uses_the_grade_cell_and_one_product_per_card(tmp_path):
    st = seed(tmp_path, {"Ungraded": [[ms(400), 140000.0], [ms(6), 160640.0]]},
              {"Ungraded": {"p": 1606.4, "c": 0.0, "vol": None}})
    m = zone.macro(st, Cat(), {6: ["zard"]}, NOW)
    assert m[6]["priced"] == 1 and m[6]["cards"] == 1
    assert round(m[6]["cap"]) == 1606


def test_the_first_edition_row_does_not_double_count_the_card(tmp_path):
    st = seed(tmp_path, {"Ungraded": [[ms(400), 1400.0], [ms(6), 1606.4]]},
              {"Ungraded": {"p": 1606.4, "c": 0.0, "vol": None}},
              extra=[("pk:EN:base1-4:1st-edition-holo@pkm-base1-4",
                      {"Ungraded": [[ms(400), 20000.0], [ms(6), 25000.0]]},
                      {"Ungraded": {"p": 25000.0, "c": 0.0, "vol": None}})])
    m = zone.macro(st, Cat(), {6: ["zard"]}, NOW)
    assert round(m[6]["cap"]) == 1606


def test_the_grid_is_gen_one_and_the_tape_counts_coverage(tmp_path):
    st = seed(tmp_path, full_series(), {"Ungraded": {"p": 1606.4, "c": 0.0, "vol": None}})
    page = zone.view_zone(st, Cat(), NOW, "USD", "USD", {})
    assert "Charizard" in page and "Chikorita" not in page
    assert "1</b> of 1 fully priced" in page and "0</b> partial" in page
    assert "1</b> cards tracked" in page
    assert "1 of 1 cards priced" in page
