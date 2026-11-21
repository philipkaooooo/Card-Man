"""The clean UI: listing photos through CardMan only, icons that always say what they mean, calm motion."""
import re
import threading
import urllib.parse
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

import pytest

from cardman import listing_detail as ld
from cardman.ui_icons import EFFECTS_CSS, SPRITE, icon, names
from tests.test_store import NOW


@pytest.fixture
def site(tmp_path, monkeypatch):
    from cardman.cardinfo import CardIndex, ImageCache
    from cardman.models import EvidenceType as ET
    from cardman.store import Store
    from cardman.ui import make_handler
    from tests.fixtures import obs
    from tests.real_catalog import DATA
    from tests.test_plain_words import _opps
    from tests.test_sold_import import CAT
    monkeypatch.setattr("cardman.engine.ROOT", tmp_path)
    monkeypatch.setattr(ld, "CACHE", tmp_path / "photos")
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return b"\xff\xd8\xff" + b"0" * 64, "image/jpeg"
    monkeypatch.setattr(ld, "_fetch", fake_fetch)
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    key = _opps(st)
    iid = key.rsplit(":", 1)[1]
    import json
    with st.db:     # make the listing look like an eBay item with photos and details
        st.db.execute("UPDATE opportunity SET key=?, payload_json=json_set(payload_json, '$.listing_id', '123456789012', '$.kind', 'ask', "
                      "'$.shipping', '0', '$.listing_key', ?) WHERE key=?", ("ebay_browse:EBAY_US:123456789012", "ebay_browse:EBAY_US:123456789012", key))
        st.db.execute("UPDATE listing SET key=?, listing_id='123456789012' WHERE key=?", ("ebay_browse:EBAY_US:123456789012", key))
    ld.save(st.db, "123456789012", {"image": {"imageUrl": "https://i.ebayimg.com/images/g/AAA/s-l500.jpg"},
                                    "additionalImages": [{"imageUrl": "https://i.ebayimg.com/images/g/BBB/s-l500.jpg"}],
                                    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"]}, NOW, "item")
    st.db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", clock=lambda: NOW, catalog=lambda: CAT, cards=cards,
                                                             images=ImageCache(tmp_path / "img", get=lambda u: (_ for _ in ()).throw(OSError()))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    get = lambda p: urllib.request.urlopen(base + p).read()
    yield get, fetched
    srv.shutdown()


PAGES = ["/", "/opportunities?v=every", "/search?q=blue-eyes", "/card/89631139", "/opp/" + urllib.parse.quote("ebay_browse:EBAY_US:123456789012", safe="")]


def test_pages_never_load_pictures_from_another_site(site):
    get, _ = site
    for p in PAGES:
        html = get(p).decode()
        srcs = re.findall(r'\ssrc="([^"]+)"', html) + re.findall(r'\sdata-(?:src|big)="([^"]+)"', html)
        assert srcs or p.startswith("/search")
        assert all(s.startswith("/") and not s.startswith("//") for s in srcs), (p, [s for s in srcs if not s.startswith("/")])


def test_listing_photos_come_through_cardman_and_the_page_has_a_gallery(site):
    get, fetched = site
    page = get(PAGES[-1]).decode()
    assert "data-gallery" in page and "/img/listing/123456789012/0.jpg?s=m" in page and "/img/listing/123456789012/1.jpg?s=l" in page
    assert "What this version looks like" in page
    body = get("/img/listing/123456789012/0.jpg?s=m")
    assert body.startswith(b"\xff\xd8") and fetched == ["https://i.ebayimg.com/images/g/AAA/s-l500.jpg"]
    get("/img/listing/123456789012/0.jpg?s=m")
    assert len(fetched) == 1                                              # second view: from the cache
    assert get("/img/listing/123456789012/7.jpg?s=m").startswith(b"<svg")   # no such photo: the placeholder, nothing fetched
    assert len(fetched) == 1


def test_every_icon_says_what_it_means(site):
    get, _ = site
    page = get("/opportunities?v=every").decode() + get(PAGES[-1]).decode()
    icons = re.findall(r'<span class="ic(?: [^"]*)?"[^>]*>', page)
    assert len(icons) >= 4
    for tag in icons:
        label = re.search(r'aria-label="([^"]+)"', tag)
        tip = re.search(r'data-tip="([^"]+)"', tag)
        assert label and tip and label.group(1) == tip.group(1) and 'tabindex="0"' in tag and 'title="' in tag, tag
    assert "Accepts offers" in page and "Buy it now" in page and "Free shipping" in page
    for p in PAGES:
        assert get(p).decode().count('<symbol id="i-ebay"') == 1                               # one icon set per page, in the page


def test_icon_set_and_motion_rules():
    for n in names():
        assert f'id="i-{n}"' in SPRITE
        assert 'aria-label="x"' in icon(n, "x")
    with pytest.raises(KeyError):
        icon("nope", "x")
    assert "prefers-reduced-motion: reduce" in EFFECTS_CSS and "animation:none!important" in EFFECTS_CSS


def test_old_cached_photos_are_removed_after_the_listing_ended(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "s.sqlite")
    db.execute("CREATE TABLE listing (listing_id TEXT, ended_at TEXT)")
    ld.save(db, "999999999999", {"image": {"imageUrl": "https://i.ebayimg.com/images/g/Z/s-l500.jpg"}}, NOW, "search")
    ld.photo(db, "999999999999", 0, "s", root=tmp_path / "c", fetch=lambda u: (b"\xff\xd8x", "image/jpeg"))
    db.execute("INSERT INTO listing VALUES ('999999999999', ?)", ((NOW - timedelta(days=10)).isoformat(),))
    assert ld.evict(db, NOW, root=tmp_path / "c") == 0                    # ended 10 days ago: kept
    db.execute("UPDATE listing SET ended_at=?", ((NOW - timedelta(days=40)).isoformat(),))
    assert ld.evict(db, NOW, root=tmp_path / "c") == 1
