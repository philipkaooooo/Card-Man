"""Card Pile: what a person set aside, and the page that prices it from its own sales."""
import json
from datetime import datetime, timezone

from cardman.pile import add, keys, remove, resolve, view
from cardman.store import Store

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)
KEY = "ebay_browse:EBAY_US:123456789012"


class M:
    def __call__(self, v, sign=False):
        return "—" if v is None else f"${v:,.0f}"


def seed(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    st.db.executescript(
        "CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
        "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE listing (key TEXT PRIMARY KEY, source TEXT, listing_id TEXT, market TEXT, title TEXT, url TEXT, seller TEXT, kind TEXT, "
        "price TEXT, currency TEXT, ends_at TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, fingerprint TEXT, data_json TEXT);")
    paid = {"title": "Blue-Eyes White Dragon LOB 1st Edition", "url": "https://www.ebay.com/itm/123456789012",
            "kinds": [], "printing_id": "p:LOB-EN001:UR:1st", "ask_home": 500.0,
            "scenarios": [{"printing_id": "p:LOB-EN001:UR:1st", "basis": "sales", "label": "1st Edition",
                           "weight": 1, "resale_mid": 800.0, "resale_low": 700.0, "profit_mid": 159.0,
                           "n_sales": 3, "all_in_home": 500.0}]}
    blind = {"title": "Dark Magician LOB no comps", "url": "https://www.ebay.com/itm/999999999999",
             "kinds": [], "printing_id": "p:LOB-EN005:UR:1st", "ask_home": 300.0, "scenarios": []}
    with st.db:
        st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (KEY, "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp", json.dumps(paid), "t", "t"))
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (KEY, "ebay_browse:EBAY_US", "123456789012", "INTL", paid["title"], paid["url"], None,
                       "ask", "500", "USD", None, "t", "t", None, "fp", "{}"))
        st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:999999999999", "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp",
                       json.dumps(blind), "t", "t"))
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:999999999999", "ebay_browse:EBAY_US", "999999999999", "INTL",
                       blind["title"], blind["url"], None, "ask", "300", "USD", None, "t", "t", None, "fp", "{}"))
    return st


def test_resolve_takes_a_link_an_item_number_or_a_key(tmp_path):
    st = seed(tmp_path)
    assert resolve(st, "https://www.ebay.com/itm/Blue-Eyes-White-Dragon/123456789012?hash=x") == KEY
    assert resolve(st, "123456789012") == KEY
    assert resolve(st, KEY) == KEY
    assert resolve(st, "123") is None and resolve(st, "") is None


def test_add_is_idempotent_and_remove_clears(tmp_path):
    st = seed(tmp_path)
    assert add(st, KEY, NOW) is True
    assert add(st, KEY, NOW) is False
    assert keys(st) == [KEY]
    remove(st, KEY)
    assert keys(st) == []


def test_view_prices_from_sales_and_flags_the_ones_without_a_comp(tmp_path):
    st = seed(tmp_path)
    add(st, KEY, NOW)
    add(st, "ebay_browse:EBAY_US:999999999999", NOW)
    page = view(st, M(), NOW, "tok")
    assert "Card Pile" in page and "Blue-Eyes White Dragon LOB 1st Edition" in page
    assert "1 of 2 price from real sales" in page
    assert "spare capital" not in page
    assert "$159" in page and "0.32×" in page                      # 159 / 500
    assert "Best return per dollar" in page
    assert "Dark Magician LOB no comps" in page and "no sale price yet" in page
    assert page.count('action="/pile/remove"') == 2
    assert "2 listings set aside" in page


def test_view_with_an_empty_pile_says_so(tmp_path):
    page = view(seed(tmp_path), M(), NOW, "tok")
    assert "The pile is empty" in page
