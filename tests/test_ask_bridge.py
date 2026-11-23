"""Stored TCGplayer asks join the daily ask table, keyed to the printing by product id and finish."""
import sqlite3
from datetime import datetime, timezone

from cardman import history
from cardman.catalog import registry
from cardman.store import Store

NOW = datetime(2026, 11, 24, 15, tzinfo=timezone.utc)
PID = "pk:EN:cubone1-1:unlimited@pkm-cubone1-1"


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "cat.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE xref (pid TEXT, source TEXT, ext_id TEXT, ext_variant TEXT,"
                "PRIMARY KEY (source, ext_id, ext_variant))")
    with con:
        con.execute("INSERT INTO xref VALUES (?,?,?,?)", (PID, "tcgplayer", "42416", "Normal"))
    con.close()
    monkeypatch.setattr(registry, "DB", path)
    st = Store(tmp_path / "e.sqlite")
    st.save_public_listings([{
        "source": "tcgplayer", "listing_id": 1, "product_id": 42416, "card_key": "tcg:42416|Normal",
        "name": "Cubone", "number": "68", "printing": "Normal", "condition": "Near Mint",
        "language": "English", "price": "5.00", "shipping": "1.00", "label": "ask", "sales": 0,
        "exit_low": None, "after_fees": None, "multiple": None, "why": "ask"}], NOW)
    return st


def test_a_tcg_ask_lands_in_ask_daily_all_in(tmp_path, monkeypatch):
    st = _setup(tmp_path, monkeypatch)
    assert history.update_ask_daily(st, NOW, "USD") == 1
    row = st.db.execute("SELECT printing_id, gkey, day, low, median, n, currency FROM ask_daily").fetchone()
    assert row == (PID, "|", "2026-11-24", 6.0, 6.0, 1, "USD")


def test_a_finish_the_catalog_does_not_know_is_dropped(tmp_path, monkeypatch):
    st = _setup(tmp_path, monkeypatch)
    with st.db:
        st.db.execute("UPDATE public_listing SET printing='Glossy' WHERE listing_id=1")
    assert history.update_ask_daily(st, NOW, "USD") == 0
