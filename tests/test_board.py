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
    prices = [float(x.replace(",", "")) for x in re.findall(r'class="i-price">[^0-9]*([0-9][0-9,.]*)', page)]
    assert prices == sorted(prices), prices
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