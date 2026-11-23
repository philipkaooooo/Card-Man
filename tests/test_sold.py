"""The sold market: outcomes recorded, classified, filterable; under-PC deals promoted into Buy; the rating hint."""
import json
import re
import urllib.parse

from cardman.store import Store
from tests.test_board import board, _add_listing
from tests.test_plain_words import BANNED, readable
from tests.test_listing_verdict import D, sc

PID = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"


def _watches(db):
    st = Store(db)
    with st.db:
        st.db.executescript(
            "CREATE TABLE IF NOT EXISTS ebay_sold_watch (item_id TEXT PRIMARY KEY, listing_key TEXT, checked_at TEXT, "
            "sold_qty INTEGER, price TEXT, currency TEXT, best_offer INTEGER, status TEXT);"
            "CREATE TABLE IF NOT EXISTS listing_event (key TEXT, at TEXT, kind TEXT, old TEXT, new TEXT);")
        w = ("2026-11-13T00:00:00+00:00",)
        rows = [
            ("buy", 3, "250", "USD", 0, "IN_STOCK"),
            ("check", 1, "250", "USD", 0, "IN_STOCK"),
            ("misid", 2, "250", "USD", 0, "IN_STOCK"),
            ("guide", 1, "250", "USD", 1, "IN_STOCK"),
            ("lot", None, None, None, 0, "GONE"),
        ]
        for suffix, qty, price, cur, bo, status in rows:
            st.db.execute("INSERT INTO ebay_sold_watch VALUES (?,?,?,?,?,?,?,?)",
                          (suffix, f"ebay_browse:EBAY_US:{suffix}", *w, qty, price, cur, bo, status))
        for old, new in (("300", "280"), ("280", "250")):
            st.db.execute("INSERT INTO listing_event VALUES (?,?,?,?,?)",
                          ("ebay_browse:EBAY_US:guide", "2026-11-11T00:00:00+00:00", "PRICE_CHANGE", old, new))
    st.db.close()
    key = _add_listing(db, "shelved", payload=D(kinds=[], scenarios=[sc(pid=PID)], all_in_home=1200.0, best_profit=-100.0))
    st = Store(db)
    with st.db:
        st.db.execute("UPDATE opportunity SET status='SHELVED' WHERE key=?", (key,))
        st.db.execute("INSERT INTO ebay_sold_watch VALUES (?,?,?,?,?,?,?,?)",
                      ("shelved", key, "2026-11-13T00:00:00+00:00", 2, "250", "USD", 0, "IN_STOCK"))
    st.db.close()
    return key


def test_sync_records_and_classifies_every_outcome(board):
    get, post, ajax, db = board
    shelved = _watches(db)
    from cardman.sold import sync, counts
    from tests.test_store import NOW

    class Money:
        home = "TWD"

        def __call__(self, v, *a):
            return f"NT${v:,.0f}" if v is not None else "—"

    st = Store(db)
    assert sync(st, Money(), NOW) == 6
    assert counts(st) == {"agreed": 3, "overcalled": 1, "dodged": 1, "missed": 1}
    row = st.db.execute("SELECT klass, why, units, cuts FROM sold_review WHERE key=?", (shelved,)).fetchone()
    assert row[0] == "missed" and "market took it" in row[1] and row[2] == 2
    g = st.db.execute("SELECT klass, why, best_offer FROM sold_review WHERE key='ebay_browse:EBAY_US:guide'").fetchone()
    assert g[0] == "overcalled" and "2 price cuts" in g[1] and g[2] == 1
    assert "upper bound" in g[1]
    lot = st.db.execute("SELECT klass, why FROM sold_review WHERE key='ebay_browse:EBAY_US:lot'").fetchone()
    assert lot[0] == "dodged" and "no sale we could prove" in lot[1]
    assert sync(st, Money(), NOW) == 6                     # rerun upserts, never duplicates
    assert st.db.execute("SELECT COUNT(*) FROM sold_review").fetchone()[0] == 6
    st.db.close()


def test_sold_section_renders_with_plain_words(board):
    get, *_ = board
    _watches(db := board[3])
    page = get("/opportunities?sec=sold")
    for piece in ("Sold market", "Market agreed", "Missed prey", "Dodged a bullet", "Overcalled",
                  "recorded outcomes", 'id="b-grid"', 'id="bulkform"', 'id="b-drawer"', 'id="b-toast"',
                  'id="s-sort"', "Our rating then", "Live asks", "2x Check", "last ask", "sale price"):
        assert piece in page, piece
    assert page.count('class="bcell srow') == 6
    text = readable(page)
    found = [w for w in BANNED if re.search(re.escape(w), text, re.I)]
    assert not found, found


def test_sold_filters_search_and_sort(board):
    get, post, ajax, db = board
    shelved = _watches(db)
    get("/opportunities?sec=sold")
    missed = get("/opportunities?sec=sold&klass=missed")
    assert shelved in missed
    assert "ebay_browse:EBAY_US:buy" not in missed
    passed = get("/opportunities?sec=sold&rating=PASS")
    assert shelved in passed and "ebay_browse:EBAY_US:check" not in passed
    found = get("/opportunities?sec=sold&q=lot")
    assert "ebay_browse:EBAY_US:lot" in found and "ebay_browse:EBAY_US:buy" not in found
    byunits = get("/opportunities?sec=sold&sort=units:d")
    first = re.search(r'class="bcell srow[^"]*" data-key="([^"]+)"', byunits)
    assert first.group(1) == "ebay_browse:EBAY_US:buy"      # 3 units leads


def test_sold_marks_pin_and_hide(board):
    get, post, ajax, db = board
    _watches(db)
    get("/opportunities?sec=sold")
    key = "ebay_browse:EBAY_US:lot"
    j = ajax("/board/mark", {"t": "tok", "keys": key, "mark": "pinned"})
    assert j["ok"] == 1
    page = get("/opportunities?sec=sold")
    first = re.search(r'class="bcell srow[^"]*" data-key="([^"]+)"', page)
    assert first.group(1) == key and "mk-pinned" in page
    ajax("/board/mark", {"t": "tok", "keys": "ebay_browse:EBAY_US:buy", "mark": "hidden"})
    assert "ebay_browse:EBAY_US:buy" not in get("/opportunities?sec=sold")
    assert "ebay_browse:EBAY_US:buy" in get("/opportunities?sec=sold&mk=hidden")


def test_under_pc_deals_count_as_buy(board):
    get, post, ajax, db = board
    key = _add_listing(db, "pcdeal", payload=D(kinds=[], printing_id=PID, scenarios=[sc(pid=PID)],
                                               all_in_home=300.0, priced_share=1.0))
    st = Store(db)
    with st.db:
        st.db.execute("CREATE TABLE pc_card (printing_id TEXT, grades TEXT, status TEXT)")
        st.db.execute("INSERT INTO pc_card VALUES (?,?,?)", (PID, json.dumps({"Ungraded": {"p": 12.0}}), "ok"))
        st.db.execute("INSERT INTO fx_rate (base, quote, mid, as_of, source, spread_pct, recorded_at) VALUES (?,?,?,?,?,?,?)",
                      ("USD", "TWD", "32", "2026-11-03T12:00:00+00:00", "test", "0", "2026-11-03T12:00:00+00:00"))
    st.db.close()
    page = get("/opportunities?grp=list")
    assert "b-pcdeal" in page
    assert "sold-market comp" in page
    assert re.search(r'l-buy[^>]*>.*?data-n="2"', page, re.S)
    lane = get("/opportunities?v=BUY&grp=list")
    assert key in lane
    assert "ebay_browse:EBAY_US:lot" not in lane


def test_rating_hint_rides_both_views(board):
    get, *_ = board
    _watches(board[3])
    for page in (get("/opportunities"), get("/opportunities?sec=sold")):
        assert 'class="rhint"' in page
        assert "best guess" in page and "whole track record" in page
        assert "kept score" in page
    live = get("/opportunities")
    assert "Track record" in live and "sec=sold" in live