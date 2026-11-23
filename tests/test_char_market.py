"""The character page's market block: TCGplayer market per version, the summed daily line, and the asks."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from cardman import char_market
from cardman.store import Store

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
PID = "pk:EN:cubone1-1:unlimited@pkm-cubone1-1"
PID2 = "pk:EN:cubone1-1:reverse-holo@pkm-cubone1-1"


def _catalog(tmp_path, monkeypatch):
    path = tmp_path / "cat.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE market_price (pid TEXT, source TEXT, day TEXT, low REAL, mid REAL, market REAL,"
        "PRIMARY KEY (pid, source, day));"
        "CREATE TABLE xref (pid TEXT, source TEXT, ext_id TEXT, ext_variant TEXT,"
        "PRIMARY KEY (source, ext_id, ext_variant));")
    with con:
        con.executemany("INSERT INTO market_price VALUES (?,?,?,?,?,?)", [
            (PID, "tcgplayer", "2026-11-21", 8.0, 9.0, 10.0),
            (PID, "tcgplayer", "2026-11-22", 8.0, 11.0, 12.0),
            (PID2, "tcgplayer", "2026-11-21", 20.0, 22.0, 25.0),
            (PID2, "tcgplayer", "2026-11-22", 20.0, 22.0, 25.0),
        ])
        con.executemany("INSERT INTO xref VALUES (?,?,?,?)", [
            (PID, "tcgplayer", "42416", "Normal"),
            (PID2, "tcgplayer", "42417", "Reverse Holofoil"),
        ])
    con.close()
    monkeypatch.setattr(char_market, "CATALOG", path)


def _chat():
    card = ("cubone1-1", "Cubone", "cubone", "base1", "68")
    versions = [NS(id=PID, edition="Unlimited"), NS(id=PID2, edition="Reverse Holo")]

    class Cat:
        _cards = {"cubone1-1": card}

        def set(self, sid):
            return {"name": "Base Set"}

        def versions(self, cid):
            return versions

        def guide(self, pid):
            return None

        def guide_history(self, pid):
            return []

        def card_name(self, cid):
            return "Cubone"

    return Cat(), versions


def _store(tmp_path, asks):
    st = Store(tmp_path / "e.sqlite")
    rows = [{"source": "tcgplayer", "listing_id": lid, "product_id": prod, "card_key": f"tcg:{prod}|x",
             "name": "Cubone", "number": "68", "printing": printing, "condition": cond, "language": "English",
             "price": price, "shipping": ship, "label": "ask", "sales": 0, "exit_low": None,
             "after_fees": None, "multiple": None, "why": "ask"} for lid, prod, printing, cond, price, ship in asks]
    st.save_public_listings(rows, NOW)
    return st


def _hx():
    return {"live": {PID: {"d": "2026-11-22", "low": 7.0}},
            "series": [{"month": [{"start": "2026-11-01", "n": 4}], "asks": [], "sales": []}]}


def test_market_low_mid_and_the_summed_move_come_from_tcgplayer(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    cat, variants = _chat()
    st = _store(tmp_path, [])
    h = char_market.section(st, cat, "Cubone", ["cubone1-1"], variants, NOW, "USD", "USD", None, _hx())
    assert "How Cubone is performing" in h
    assert "$37.00" in h                     # 12 + 25, summed on the latest day
    assert "+20.0%" in h                     # 10 -> 12 on the unlimited print
    assert "$12.00" in h and "$25.00" in h
    assert "Reverse Holo" in h


def test_a_tcg_listing_under_market_is_flagged_with_its_source(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    cat, variants = _chat()
    st = _store(tmp_path, [(1, "42416", "Normal", "Near Mint", 6.0, 1.0)])
    h = char_market.section(st, cat, "Cubone", ["cubone1-1"], variants, NOW, "USD", "USD", None, _hx())
    assert "listed under its market" in h
    assert "TCG" in h
    assert "-41.7%" in h or "-42" in h       # 7.00 all-in against a 12.00 market
    assert "Listed under market" in h


def test_an_ask_is_never_matched_across_products(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    cat, variants = _chat()
    st = _store(tmp_path, [(2, "99999", "Normal", "Near Mint", 1.0, 0.0)])
    hx = _hx()
    hx["live"] = {}
    h = char_market.section(st, cat, "Cubone", ["cubone1-1"], variants, NOW, "USD", "USD", None, hx)
    assert "No stored ask sits more than 3% under its market" in h
    assert "TCG" not in h.replace("TCGplayer", "")


def test_no_market_anywhere_draws_nothing(tmp_path, monkeypatch):
    _catalog(tmp_path, monkeypatch)
    cat, variants = _chat()
    st = _store(tmp_path, [])
    monkeypatch.setattr(char_market, "_market", lambda pids: {})
    empty = char_market.section(st, cat, "Cubone", ["cubone1-1"], variants, NOW, "USD", "USD", None, _hx())
    assert empty == ""
