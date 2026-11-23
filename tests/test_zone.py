"""The Characters tab: PriceCharting macro stats per Pokémon, the sort chips, and the pokedex tiles."""
import json
from datetime import datetime, timezone

import pytest

from cardman.store import Store
from cardman.zone import entries, macro, view_zone
from tests.pkm_fixture import catalog

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)
CHAR = "pk:EN:base1-4:1st-edition-holo@pkm-base1-4"
CHAR2 = "pk:EN:base1-4:unlimited-holo@pkm-base1-4"
PIKA = "pk:EN:sv3pt5-25:normal@pkm-sv3pt5-25"


@pytest.fixture(scope="module")
def cat(tmp_path_factory):
    return catalog(tmp_path_factory.mktemp("pkm"))


def _store(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    st.db.executescript(
        "CREATE TABLE pc_card(printing_id TEXT PRIMARY KEY, url TEXT, grades TEXT, series TEXT, pop TEXT, "
        "fetched TEXT, status TEXT);"
        "CREATE TABLE price_trend(printing_id TEXT, gkey TEXT, game TEXT, sales_month REAL);")
    with st.db:
        st.db.execute("INSERT INTO pc_card VALUES (?,?,?,?,?,?,?)",
                      (CHAR, None, "{}", json.dumps({"Ungraded": [[100, 100], [200, 200]]}), "{}", NOW.isoformat(), "ok"))
        st.db.execute("INSERT INTO pc_card VALUES (?,?,?,?,?,?,?)",
                      (CHAR2, None, "{}", json.dumps({"Ungraded": [[50, 50]]}), "{}", NOW.isoformat(), "ok"))
        st.db.execute("INSERT INTO price_trend VALUES (?,?,?,?)", (CHAR, "|", "pkm", 3.0))
        st.db.execute("INSERT INTO price_trend VALUES (?,?,?,?)", (PIKA, "|", "pkm", 2.0))
        st.db.execute("INSERT INTO price_trend VALUES (?,?,?,?)", (PIKA, "PSA|10", "pkm", 9.0))
    return st


def test_entries_merge_every_spelling_under_one_dex_number(cat):
    rows = {e["name"]: e for e in entries(cat)}
    assert "Charizard" in rows and "Pikachu" in rows
    assert "Professor's Research" not in rows                      # a Trainer is not a Pokémon
    char = rows["Charizard"]
    assert {"base1-4", "sv3pt5-199", "sv3pt5-6", "sv4pt5-234", "sma-SV49"} <= set(char["cids"])


def test_macro_sums_priced_printings_and_counts_the_history_honestly(cat, tmp_path):
    st = _store(tmp_path)
    by_dex = {e["dex"]: e["cids"] for e in entries(cat)}
    stats = macro(st, cat, by_dex, NOW)
    char = next(s for s in stats.values() if s["versions"] and s["priced"] == 2)
    assert char["cap"] == 250 and char["top"] == 200            # 200 (1st ed) + 50 (unlimited)
    assert char["move"] is None                                 # two points from 1970 are not a 12-month move
    assert char["pace"] == 3.0                                  # raw rows only; the PSA 10 row on Pikachu is not double-counted
    ray = next(s for s in stats.values() if s["priced"] == 0)
    assert ray["cap"] == 0 and ray["move"] is None


def test_view_sorts_by_cap_and_lists_unpriced_last(cat, tmp_path):
    st = _store(tmp_path)
    h = view_zone(st, cat, NOW, "TWD", "TWD", {"sort": ["cap"]})
    assert 'id="cfind"' in h and 'placeholder="Find a character"' in h
    assert all(lab in h for lab in ('Dex order', 'Market cap', 'Top card', '12-mo move', 'Sales pace'))
    assert '<a class="zchip on"' in h and 'aria-current="true"' in h
    first = h.index('class="zd"')
    assert 'data-name="charizard"' in h[first:first + 300]
    assert "no PC yet" in h
    assert f'<b>$250</b>' in h
    assert "<b>0</b> of 3 fully priced" in h and "<b>1</b> partial" in h
    assert "Umbreon" not in h                                  # dex 197 sits outside the first 151


def test_view_dex_order_starts_at_bulbasaur_and_shows_the_plate(cat, tmp_path):
    st = _store(tmp_path)
    h = view_zone(st, cat, NOW, "TWD", "TWD", {})
    first = h.index('class="zd"')
    assert 'data-name="bulbasaur"' in h[first:first + 300]
    assert 'class="zd"' in h and 'class="zplate"' in h and 'class="zid"' in h
    assert ">Bulbasaur</b><em" in h
    assert "--g:#" in h
