"""The Listings board: facets, sort studio, marks, labels, saved views, bulk handling, undo."""
import json
import re
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from cardman.cardinfo import CardIndex, ImageCache
from cardman.store import Store
from cardman.ui import make_handler
from tests.real_catalog import DATA
from tests.test_plain_words import BANNED, _opps, readable
from tests.test_sold_import import CAT
from tests.test_store import NOW


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr("cardman.engine.ROOT", tmp_path)
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    _opps(st)
    st.db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", clock=lambda: NOW, catalog=lambda: CAT, cards=cards,
                                                             images=ImageCache(tmp_path / "img", get=lambda u: (_ for _ in ()).throw(OSError()))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(p):
        return urllib.request.urlopen(base + p).read().decode()

    def post(p, fields):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        req = urllib.request.Request(base + p, data=data)
        return urllib.request.urlopen(req).read().decode()

    def ajax(p, fields):
        return json.loads(post(p, {**fields, "ajax": "1"}))

    yield get, post, ajax, db
    srv.shutdown()


def test_board_renders_the_working_surface(board):
    get, *_ = board
    page = get("/opportunities")
    for piece in ('class="brail"', 'class="bsort"', 'id="bs-list"', 'id="bulkform"', 'id="b-drawer"',
                  'id="b-toast"', 'class="bviews"', 'id="b-q"', 'id="b-grid"',
                  "Your marks", "First seen", "Total to you", "Save view", "pick page"):
        assert piece in page, piece
    text = readable(page)
    found = [w for w in BANNED if re.search(re.escape(w), text, re.I)]
    assert not found, found
    assert "2x Check" in page


def test_marks_pin_to_top_and_hide_with_undo(board):
    get, post, ajax, db = board
    key = "ebay_browse:EBAY_US:lot"
    j = ajax("/board/mark", {"t": "tok", "keys": key, "mark": "pinned"})
    assert j["ok"] == 1 and j["prev"][key] == ""
    page = get("/opportunities")
    first_cell = re.search(r'class="bcell[^"]*" data-key="([^"]+)"', page)
    assert first_cell.group(1) == key
    assert "mk-pinned" in page
    j = ajax("/board/mark", {"t": "tok", "keys": key, "mark": "hidden"})
    assert j["prev"][key] == "pinned"
    assert key not in get("/opportunities")
    assert key in get("/opportunities?mk=hidden")
    j = ajax("/board/mark", {"t": "tok", "restore": json.dumps({key: "pinned"})})
    assert j["ok"] == 1
    assert key in get("/opportunities")
    st = Store(db)
    assert st.db.execute("SELECT mark FROM board_mark WHERE key=?", (key,)).fetchone()[0] == "pinned"
    st.db.close()


def test_bulk_takes_repeated_checkbox_keys(board):
    get, post, ajax, db = board
    keys = ["ebay_browse:EBAY_US:buy", "ebay_browse:EBAY_US:check"]
    j = json.loads(post("/board/bulk", {"t": "tok", "act": "reviewed", "keys": keys, "ajax": "1"}))
    assert j["ok"] == 1 and set(j["prev"]) == set(keys)
    page = get("/opportunities?mk=reviewed")
    assert page.count("mk-reviewed") >= 2


def test_labels_land_and_filter(board):
    get, post, ajax, db = board
    key = "ebay_browse:EBAY_US:buy"
    j = ajax("/board/label", {"t": "tok", "keys": key, "label": "chase", "color": "green"})
    assert j["ok"] == 1 and j["added"] == [key]
    page = get("/opportunities")
    assert 'class="blab c-green">chase' in page
    assert key in get("/opportunities?lab=chase")
    assert "ebay_browse:EBAY_US:check" not in get("/opportunities?lab=chase")
    ajax("/board/unlabel", {"t": "tok", "keys": key, "label": "chase"})
    assert 'class="blab c-green">chase' not in get("/opportunities")


def test_saved_views_apply_and_come_back_after_delete(board):
    get, post, ajax, db = board
    j = ajax("/board/view", {"t": "tok", "name": "Cheap checks", "qs": "v=CHECK&sort=ask:a", "game": "ygo"})
    vid = j["id"]
    page = get(f"/opportunities?view={vid}")
    assert "Cheap checks" in page
    assert 'aria-current="true"' in page
    gone = ajax("/board/viewdel", {"t": "tok", "id": str(vid)})
    assert gone["ok"] == 1 and gone["view"]["name"] == "Cheap checks"
    assert "Cheap checks" not in get("/opportunities")
    j2 = ajax("/board/view", {"t": "tok", "id": str(vid), "name": gone["view"]["name"],
                              "qs": gone["view"]["qs"], "game": "ygo"})
    assert j2["id"] == vid
    assert "Cheap checks" in get("/opportunities")


def test_sort_and_price_band_and_search(board):
    get, *_ = board
    page = get("/opportunities?sort=ask:a")
    prices = [float(x.replace(",", "")) for x in re.findall(r'class="bt-price"><b>[^0-9]*([0-9][0-9,.]*)', page)]
    assert prices and prices == sorted(prices), prices
    narrow = get("/opportunities?q=lot")
    assert "ebay_browse:EBAY_US:lot" in narrow
    assert "ebay_browse:EBAY_US:buy" not in narrow
    band = get("/opportunities?pmin=1&pmax=2")
    assert "Nothing matches" in band


def test_token_is_required(board):
    get, post, ajax, db = board
    with pytest.raises(urllib.error.HTTPError) as x:
        post("/board/mark", {"keys": "ebay_browse:EBAY_US:buy", "mark": "pinned"})
    assert x.value.code == 403

def _add_listing(db, suffix, *, payload=None, data="{}", title=None):
    st = Store(db)
    key = f"ebay_browse:EBAY_US:{suffix}"
    t = title or f"Blue-Eyes White Dragon LOB-EN001 {suffix}"
    with st.db:
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "ebay_browse:EBAY_US", suffix, "INTL", t, "https://www.ebay.com/itm/1", None, "ask", "250", "USD", None,
                       "2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00", None, "f", data))
        if payload is not None:
            st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key, "SPECTATE", "OPEN", 1.0, None, 0.0, json.dumps(payload.get("kinds")), payload.get("printing_id"),
                           None, "fp", json.dumps(payload), "t", "t"))
    st.db.close()
    return key


def test_condition_badges_name_the_grade_and_where_it_came_from(board):
    get, post, ajax, db = board
    st = Store(db)
    with st.db:
        st.db.execute("UPDATE listing SET data_json=? WHERE key=?", (json.dumps({"condition": "Near Mint"}), "ebay_browse:EBAY_US:buy"))
        st.db.execute("UPDATE listing SET title=? WHERE key=?", ("Blue-Eyes White Dragon LOB-EN001 Lightly Played", "ebay_browse:EBAY_US:check"))
    st.db.close()
    page = get("/opportunities")
    assert "<b>NM</b> Near Mint" in page
    assert "condition field" in page
    assert "<b>LP</b> Lightly Played" in page
    assert "named in the listing title" in page
    assert "Condition unstated" in page
    narrow = get("/opportunities?cond=NM")
    assert "ebay_browse:EBAY_US:buy" in narrow
    assert "ebay_browse:EBAY_US:check" not in narrow


def test_unchecked_lane_holds_listings_the_engine_has_not_priced(board):
    get, post, ajax, db = board
    key = _add_listing(db, "fresh", data=json.dumps({"game": "ygo"}))
    page = get("/opportunities")
    assert "Unchecked" in page and "l-new" in page
    lane = get("/opportunities?v=NEW")
    assert key in lane
    assert "ebay_browse:EBAY_US:buy" not in lane
    assert "b-new" in lane


def test_group_by_card_collapses_asks_and_opens_them(board):
    from tests.test_listing_verdict import D, sc
    get, post, ajax, db = board
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    for n, ask in (("ask2", 310.0), ("ask3", 320.0)):
        _add_listing(db, n, payload=D(kinds=["UNDERPRICED"], printing_id=pid, scenarios=[sc(pid=pid)],
                                      all_in_home=ask, priced_share=1.0))
    page = get("/opportunities")
    assert "3 asks" in page
    assert page.count('class="bcell') == 5
    assert "One by one" in page and "By card" in page
    flat = get("/opportunities?grp=list")
    assert flat.count('class="bcell') == 7
    one = get("/opportunities?print=" + urllib.parse.quote(pid, safe=""))
    assert one.count('class="bcell') == 3
    assert "Every ask of one card" in one and "Back to all cards" in one


def test_under_pc_badge_facet_and_sort(board):
    get, post, ajax, db = board
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    st = Store(db)
    with st.db:
        st.db.execute("CREATE TABLE pc_card (printing_id TEXT, grades TEXT, status TEXT)")
        st.db.execute("INSERT INTO pc_card VALUES (?,?,?)", (pid, json.dumps({"Ungraded": {"p": 12.0}}), "ok"))
        st.db.execute("INSERT INTO fx_rate (base, quote, mid, as_of, source, spread_pct, recorded_at) VALUES (?,?,?,?,?,?,?)",
                      ("USD", "TWD", "32", "2026-11-03T12:00:00+00:00", "test", "0", "2026-11-03T12:00:00+00:00"))
    st.db.close()
    page = get("/opportunities")
    assert re.search(r"Under PC −\d+%", page)
    assert "Under the PC market" in page
    only = get("/opportunities?pc=1")
    assert "ebay_browse:EBAY_US:buy" in only
    assert "ebay_browse:EBAY_US:lot" not in only
    bypc = get("/opportunities?sort=pc:a")
    first = re.search(r'class="bcell[^"]*" data-key="([^"]+)"', bypc)
    assert first.group(1) == "ebay_browse:EBAY_US:buy"


def test_empty_board_offers_a_way_out_and_is_marked_for_memory(board):
    get, *_ = board
    page = get("/opportunities")
    assert 'data-empty="0"' in page
    dead = get("/opportunities?v=NEW")
    assert 'data-empty="1"' in dead
    assert "Show everything" in dead
    assert 'href="/opportunities"' in dead


def test_home_hunt_passes_on_a_measured_discount_not_a_flag(board):
    get, post, ajax, db = board
    from tests.test_listing_verdict import D, sc
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    # resolved and under the sold ceiling, but the engine never tagged UNDERPRICED -> still a deal
    _add_listing(db, "deal", payload=D(kinds=[], scenarios=[sc(pid=pid)], printing_id=pid, all_in_home=400.0, priced_share=1.0))
    # a hair over the ceiling is fair market, worth a look, not a skip
    _add_listing(db, "fair", payload=D(kinds=[], scenarios=[sc(pid=pid, max_buy=300.0)], printing_id=pid, all_in_home=320.0, priced_share=1.0))
    page = get("/")
    assert re.search(r'class="hun card sig-good" href="[^"]*"[^>]*data-key="ebay_browse:EBAY_US:deal"', page)
    assert re.search(r'class="hun card sig-avg" href="[^"]*"[^>]*data-key="ebay_browse:EBAY_US:fair"', page)
    assert "Under what to pay" in page and "Worth a second look" in page


def test_home_hunt_counts_an_under_pc_ask_with_no_sales_as_a_deal(board):
    get, post, ajax, db = board
    from tests.test_listing_verdict import D, sc
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    _add_listing(db, "pcdeal", payload=D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference", pid=pid)],
                                         printing_id=pid, all_in_home=280.0, priced_share=1.0))
    st = Store(db)
    with st.db:
        st.db.execute("CREATE TABLE pc_card (printing_id TEXT, grades TEXT, status TEXT)")
        st.db.execute("INSERT INTO pc_card VALUES (?,?,?)", (pid, json.dumps({"Ungraded": {"p": 12.0}}), "ok"))
        st.db.execute("INSERT INTO fx_rate (base, quote, mid, as_of, source, spread_pct, recorded_at) VALUES (?,?,?,?,?,?,?)",
                      ("USD", "TWD", "32", "2026-11-03T12:00:00+00:00", "test", "0", "2026-11-03T12:00:00+00:00"))
    st.db.close()
    page = get("/")
    assert re.search(r'class="hun card sig-good" href="[^"]*"[^>]*data-key="ebay_browse:EBAY_US:pcdeal"', page)
    assert 'bi-src pc' in page


def test_home_labels_the_grade_and_collapses_identical_asks(board):
    get, post, ajax, db = board
    from tests.test_listing_verdict import D, sc
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    # two asks of the same graded card at the same total: one tile, counted twice
    for n in ("dup1", "dup2"):
        _add_listing(db, n, payload=D(kinds=[], scenarios=[sc(pid=pid)], printing_id=pid, all_in_home=400.0, priced_share=1.0,
                                      grade_company="PSA", grade="10"))
    # a low slab grade is still graded, never raw
    _add_listing(db, "psa1", payload=D(kinds=[], scenarios=[sc(pid=pid)], printing_id=pid, all_in_home=410.0, priced_share=1.0,
                                       grade_company="PSA", grade="1"))
    page = get("/")
    assert 'class="card-cond g-graded tier-gem" title="PSA 10"' in page
    assert 'class="card-cond g-graded tier-low" title="PSA 1"' in page
    assert '<span class="cc-num">10</span>' in page
    assert 'class="card-cond g-raw' in page
    assert re.search(r'class="card-rep"[^>]*>\u00d72<', page)
    keys = re.findall(r'class="hun card sig-\w+" href="[^"]*"[^>]*data-key="ebay_browse:EBAY_US:(dup\d)"', page)
    assert len(keys) == 1, keys


def test_wizard_offers_a_run_the_hunt_hover(board):
    get, *_ = board
    page = get("/")
    assert 'class="wiztip" aria-hidden="true">Run the Hunt<' in page
    assert 'title="Run the Hunt"' in page


def test_board_tiles_are_image_led_cards_with_a_hover_peek(board):
    get, *_ = board
    page = get("/opportunities")
    for piece in ('class="btile', 'class="bt-art"', 'class="bt-peek"', 'bt-name"',
                  'class="bt-price"', 'class="bt-pill', 'id="b-sortby"', 'class="bstudio"', 'class="fmore"'):
        assert piece in page, piece
    assert page.count('class="btile') == page.count('class="bcell')
    assert 'a class="dtitle bt-name"' in page or 'class="dtitle bt-name"' in page
    assert "Open the full listing" in page


def test_sort_select_offers_presets_and_marks_the_active_one(board):
    get, *_ = board
    page = get("/opportunities?sort=ask:a")
    assert 'id="b-sortby"' in page
    assert '<option value="ask:a" selected>Lowest price</option>' in page
    assert "Best deals first" in page and "Fine-tune" in page
    custom = get("/opportunities?sort=ask:a,profit:d")
    assert "Custom order" in custom


def test_clash_photo_leads_the_art_instead_of_the_wrong_official_card():
    from cardman.ui_listing import pair_art
    d = {"photo": "clash", "kinds": [], "printing_id": "p1", "title": "A different card"}
    html = pair_art("ebay:123", d, {"123": 1})
    assert html.index("shot-real") < html.index("shot-off")
    assert "Seller photo" in html and "the matched card differs" in html
    big = pair_art("ebay:123", d, {"123": 1}, big=True)
    assert 'class="shot big clash"' in big
    match = pair_art("ebay:123", {**d, "photo": "match"}, {"123": 1})
    assert match.index("shot-off") < match.index("shot-real")
