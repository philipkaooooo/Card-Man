"""The card page's 'Listings now': which listings are this card, grouping, filters, lots, coverage. Made-up data only."""
import json
from datetime import datetime, timezone

from cardman import card_market as cm
from cardman.store import Store

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _store(tmp_path, rows):
    st = Store(tmp_path / "s.sqlite")
    st.db.executescript(
        "CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
        "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE listing (key TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, kind TEXT, price TEXT, currency TEXT, "
        "data_json TEXT, ended_at TEXT);")
    with st.db:
        for key, source, sc, all_in, kw in rows:
            d = {"scenarios": [{"printing_id": p, "weight": w, "label": f"{p} · Ultra Rare · 1st"} for p, w in sc],
                 "kinds": kw.get("kinds", []), "all_in_home": all_in}
            st.db.execute("INSERT INTO opportunity VALUES (?,?,?,1,NULL,0,'[]',NULL,NULL,'fp',?,'t','t')",
                          (key, "S", kw.get("status", "OPEN"), json.dumps(d)))
            st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?)",
                          (key, source, f"T-{key}-T", "https://example.test/" + key, kw.get("kind", "ask"), "10", "USD",
                           json.dumps({"grade_company": kw.get("gc")}), kw.get("ended")))
    return st


ROWS = [
    ("a", "ebay_browse", [("p:A@1", 1.0)], 300, {}),
    ("b", "ebay_browse", [("p:B@1", 0.6), ("p:A@1", 0.4)], 100, {}),                 # this card, printing open
    ("c", "yahoo", [("p:A@1", 0.5), ("p:Z@2", 0.5)], 50, {"kind": "auction"}),        # might be this card
    ("d", "ebay_browse", [("p:A@1", 1.0)], 20, {"kinds": ["LOT"]}),                   # a lot
    ("e", "ebay_browse", [("p:A@1", 1.0)], 5, {"ended": "2026-11-01"}),               # ended: never shown
    ("f", "ebay_browse", [("p:A@1", 1.0)], 7, {"status": "REJECTED"}),                # you rejected it
    ("g", "mercari", [("p:A@1", 1.0)], None, {"gc": "PSA"}),                          # no all-in yet: sorts last
]


def test_listings_for_a_card(tmp_path):
    st = _store(tmp_path, ROWS)
    got = cm.listings_for(st, "1")
    assert [r["key"] for r in got] == ["d", "c", "b", "a", "g"]
    by = {r["key"]: r for r in got}
    assert by["b"]["card_prob"] == 1.0 and by["b"]["printing_id"] == "p:B@1" and by["b"]["printing_prob"] == 0.6
    assert by["c"]["card_prob"] == 0.5 and by["c"]["site"] == "yahoo"
    assert by["d"]["lot"] and not by["a"]["lot"]


def test_search_summary_counts_only_sure_singles(tmp_path):
    st = _store(tmp_path, ROWS)
    assert cm.summary(st, ["1", "2"]) == {"1": (3, 100.0)}     # a, b, g; not the maybe, lot, ended or rejected


def test_section_keeps_groups_apart_counts_lots_and_filters(tmp_path):
    from cardman.ui_home import _market_section
    st = _store(tmp_path, ROWS)
    market = cm.listings_for(st, "1")
    M = lambda v, sign=False: "—" if v is None else f"${v:,.0f}"
    html = _market_section(st, "1", market, [], M, NOW, {}, None)
    this, maybe = html.split("Might be this card")
    assert "T-a-T" in this and "T-b-T" in this and "T-c-T" not in this
    assert "T-c-T" in maybe
    assert "1 lot(s)" in html and "T-d-T" not in html and "T-e-T" not in html and "T-f-T" not in html
    assert this.index("T-b-T") < this.index("T-a-T") < this.index("T-g-T")
    only_raw = _market_section(st, "1", market, [], M, NOW, {"graded": "raw"}, None)
    assert "T-g-T" not in only_raw and "T-a-T" in only_raw
    only_yahoo = _market_section(st, "1", market, [], M, NOW, {"platform": "yahoo"}, None)
    assert "T-a-T" not in only_yahoo and "T-c-T" in only_yahoo


def test_coverage_line_says_only_what_we_hold(tmp_path):
    st = _store(tmp_path, ROWS)
    line = cm.coverage_line(cm.coverage(st))
    assert line.startswith("eBay · Yahoo! Auctions · Mercari live")
    assert cm.coverage_line({"yahoo": {"live": 0, "sold": 4}}).startswith("Yahoo! Auctions sold only")
    assert "Mercari: nothing yet" in cm.coverage_line({"ebay": {"live": 3, "sold": 0}})


def test_printing_filter_is_one_dropdown_and_filters(tmp_path):
    from cardman.ui_home import _market_section
    rows = ROWS + [("h", "ebay_browse", [("p:B@1", 1.0)], 60, {})]
    st = _store(tmp_path, rows)
    market = cm.listings_for(st, "1")
    M = lambda v, sign=False: "—" if v is None else f"${v:,.0f}"
    html = _market_section(st, "1", market, [], M, NOW, {"printing": "p:B@1"}, None)
    assert html.count('<select name="printing">') == 1 and 'value="p:B@1" selected' in html
    assert "T-h-T" in html and "T-a-T" not in html
