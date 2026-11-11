"""Sold-page import, on SYNTHETIC pages modelled on each site's markup (tests/data/sold_pages). Never market evidence."""
import json
import plistlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from cardman.evidence import select_comps
from cardman.models import EvidenceType as ET
from cardman.sold_import import batch_rows, import_pages
from cardman.sold_pages import read_page
from tests.test_engine import CAT, LOB, LOB_1ST, make

PAGES = Path(__file__).parent / "data" / "sold_pages"
NOW = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
read = lambda name: read_page(name, (PAGES / name).read_bytes(), NOW)


def test_ebay_results_rows_prices_dates_shipping_and_skips():
    p = read("ebay_sold_results.html")
    assert p.site == "ebay_results" and p.synthetic
    by = {r.item_id: r for r in p.rows}
    assert set(by) == {"226000000001", "226000000002", "226000000003", "226000000005"}      # ad and price range skipped
    a = by["226000000001"]
    assert (a.title, a.price, a.currency, a.shipping, a.kind) == ("Blue-Eyes White Dragon LOB-EN001 1st Edition Ultra Rare LP", D("1050.00"), "USD", D("5.00"), "sold")
    assert a.sold_at == datetime(2026, 10, 20, tzinfo=timezone.utc) and a.lineage == "ebay:226000000001"
    assert by["226000000002"].kind == "best_offer_hidden" and by["226000000002"].shipping == D(0)
    assert by["226000000003"].flags()["is_lot"] and by["226000000005"].flags()["grade_company"] == "PSA"


def test_new_ebay_layout_and_item_page():
    c = read("ebay_sold_cards.html")
    assert [(r.item_id, r.price, r.shipping) for r in c.rows] == [("236000000010", D("310.00"), D("4.50"))]
    i = read("ebay_item_sold.html")
    assert [(r.item_id, r.price, r.sold_at.date().isoformat()) for r in i.rows] == [("226000000020", D("240.00"), "2026-10-06")]


def test_130point_reveals_the_accepted_offer_and_links_the_ebay_item():
    p = read("130point_results.html")
    by = {r.item_id: r for r in p.rows}
    assert by["226000000002"].kind == "accepted_offer" and by["226000000002"].price == D("812.00")
    assert by["226000000002"].lineage == "ebay:226000000002" and by["226000000002"].sold_at.date().isoformat() == "2026-10-18"


def test_yahoo_closed_results_in_yen_with_year_inferred():
    p = read("yahoo_closed.html")
    a = next(r for r in p.rows if r.item_id == "x1100000001")
    assert (a.price, a.currency, a.kind) == (D("12000"), "JPY", "sold")
    assert a.sold_at == datetime(2026, 10, 28, 12, 30, tzinfo=timezone.utc)       # 21:30 JST
    assert next(r for r in p.rows if r.item_id == "c1100000002").flags()["is_lot"]


def test_mercari_only_sold_items_and_the_date_is_flagged_as_capture():
    p = read("mercari_sold.html")
    assert [(r.item_id, r.price, r.currency, r.title) for r in p.rows] == [("m10000000001", D("3000"), "JPY", "青眼の白龍 LOB-JP001 ウルトラ")]
    assert p.rows[0].date_is_capture


def test_people_are_removed_from_the_stored_page():
    for name, who in (("ebay_sold_results.html", "cardshop_99"), ("ebay_sold_results.html", "vintage_vault"), ("ebay_item_sold.html", "blue_eyes_shop"),
                      ("130point_results.html", "vintage_vault"), ("yahoo_closed.html", "kaitori_shop"), ("mercari_sold.html", "some_user"),
                      ("ebay_sold_cards.html", "magic_seller")):
        p = read(name)
        assert who not in p.stored_html, (name, who)
        assert "<script" not in p.stored_html


def test_safari_webarchive_is_read_like_html():
    html = (PAGES / "ebay_sold_cards.html").read_bytes()
    arch = plistlib.dumps({"WebMainResource": {"WebResourceData": html, "WebResourceTextEncodingName": "UTF-8", "WebResourceMIMEType": "text/html",
                                               "WebResourceURL": "https://www.ebay.com/sch/i.html", "WebResourceFrameName": ""}})
    assert read_page("x.webarchive", arch, NOW).rows[0].item_id == "236000000010"


def test_unknown_pages_are_refused_by_name(tmp_path):
    e = make(tmp_path)
    res = import_pages(e, [("notes.html", b"<html><body>hello</body></html>")], NOW)
    assert res["rows"] == 0 and "not a sold-results page" in res["errors"][0]


def _all(tmp_path):
    e = make(tmp_path)
    files = [(n, (PAGES / n).read_bytes()) for n in ("ebay_sold_results.html", "130point_results.html", "yahoo_closed.html", "mercari_sold.html")]
    return e, import_pages(e, files, NOW)


def test_import_records_sales_once_and_identity_stays_open(tmp_path):
    e, res = _all(tmp_path)
    assert res["rows"] == 4 + 2 + 2 + 1 and res["new"] == res["rows"]
    again = import_pages(e, [("ebay_sold_results.html", (PAGES / "ebay_sold_results.html").read_bytes())], NOW + timedelta(hours=1))
    assert again["new"] == 0                                    # same items: nothing new
    rows = batch_rows(e.store, res["batch"])
    sale = next(r for r in rows if r["obs"].listing_id == "226000000001")
    assert len(sale["obs"].printing_candidates) > 1             # a title never resolves a printing on its own
    assert e.store.observations_for(LOB_1ST, as_of=NOW) == [] or all(o.printing_candidates != (LOB_1ST,) for o in e.store.observations_for(LOB_1ST, as_of=NOW))


def test_no_username_is_stored_anywhere(tmp_path):
    e, _ = _all(tmp_path)
    blob = (Path(e.store_path).read_bytes() + b"".join(p.read_bytes() for p in Path(tmp_path / "raw").rglob("*") if p.is_file()))
    for who in (b"cardshop_99", b"vintage_vault", b"kaitori_shop", b"some_user"):
        assert who not in blob


def test_a_confirmed_sale_prices_that_printing_from_then_on_and_duplicates_count_once(tmp_path):
    e, res = _all(tmp_path)
    rows = batch_rows(e.store, res["batch"])
    later = NOW + timedelta(minutes=5)
    for r in rows:
        if r["obs"].listing_id in ("226000000001", "226000000002"):
            e.store.confirm_identity(r["obs"].id, LOB_1ST, actor="test", now=later)
    pool = e.store.observations_for(LOB_1ST, as_of=later + timedelta(minutes=1))
    # These pages are synthetic test fixtures: real valuations never see them, so the check opts in.
    comps, rejected = select_comps(pool, LOB_1ST, as_of=later + timedelta(minutes=1), include_synthetic=True)
    # 226..01 appears on eBay and 130point (one sale); 226..02's hidden Best Offer is beaten by 130point's real amount.
    assert sorted(c.original_amount for c in comps) == [D("812.00"), D("1050.00")]
    assert any("duplicate" in why for _, why in rejected)
    assert any(c.evidence_type == ET.ACCEPTED_OFFER for c in comps)
    before = e.store.observations_for(LOB_1ST, as_of=NOW)
    assert not select_comps(before, LOB_1ST, as_of=NOW, include_synthetic=True)[0]      # confirmations do not reach back in time
    assert not select_comps(pool, LOB_1ST, as_of=later + timedelta(minutes=1))[0]       # and synthetic pages never price for real


def test_hidden_best_offer_alone_is_never_a_price(tmp_path):
    e = make(tmp_path)
    res = import_pages(e, [("ebay_sold_results.html", (PAGES / "ebay_sold_results.html").read_bytes())], NOW)
    r = next(x for x in batch_rows(e.store, res["batch"]) if x["obs"].listing_id == "226000000002")
    assert r["obs"].evidence_type == ET.SELLER_CLAIM
    e.store.confirm_identity(r["obs"].id, LOB_1ST, actor="test", now=NOW)
    assert select_comps(e.store.observations_for(LOB_1ST, as_of=NOW), LOB_1ST, as_of=NOW, include_synthetic=True)[0] == []


def test_lots_are_flagged_and_never_comparables(tmp_path):
    e = make(tmp_path)
    res = import_pages(e, [("ebay_sold_results.html", (PAGES / "ebay_sold_results.html").read_bytes())], NOW)
    lot = next(x for x in batch_rows(e.store, res["batch"]) if x["obs"].listing_id == "226000000003")
    assert lot["obs"].is_lot


# -- through the UI ----------------------------------------------------------------------------------------------------
import re as _re
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

from cardman.cardinfo import CardIndex, ImageCache
from cardman.store import Store
from cardman.ui import make_handler
from tests.real_catalog import DATA


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr("cardman.engine.ROOT", tmp_path)            # raw pages and inbox stay in the test folder
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "cost_profiles_ASSUMED.json").write_text((Path(__file__).parent.parent / "examples" / "cost_profiles_ASSUMED.json").read_text())
    db = str(tmp_path / "s.sqlite")
    Store(db).db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", catalog=lambda: CAT, cards=cards,
                                                             images=ImageCache(tmp_path / "img", get=lambda u: (_ for _ in ()).throw(OSError()))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return r.read().decode()

    def post(path, fields=None, files=None):
        if files is not None:
            b = uuid.uuid4().hex
            body = b""
            for k, v in {**(fields or {}), "t": "tok"}.items():
                body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for name, data in files:
                body += f'--{b}\r\nContent-Disposition: form-data; name="pages"; filename="{name}"\r\nContent-Type: text/html\r\n\r\n'.encode() + data + b"\r\n"
            body += f"--{b}--\r\n".encode()
            req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        else:
            req = urllib.request.Request(base + path, data=urllib.parse.urlencode({**fields, "t": "tok"}).encode(), method="POST")
        with urllib.request.urlopen(req) as r:
            return r.geturl(), r.read().decode()
    yield get, post, db
    srv.shutdown()


def test_import_page_upload_review_and_pick(ui):
    get, post, db = ui
    assert "Drop saved pages here" in get("/import") and "How to save a sold-results page" in get("/import")
    url, body = post("/import/pages", files=[(n, (PAGES / n).read_bytes()) for n in ("ebay_sold_results.html", "130point_results.html")])
    assert "/import?batch=" in url and "Read 6 sale(s) from 2 page(s)" in body and "synthetic" in body
    assert "Pick the printing" in body and "Kept as evidence only" in body
    form = _re.search(r'action="/obs/([^"]+)/identify"', body)
    assert form
    oid = urllib.parse.unquote(form.group(1))
    url, body = post(f"/obs/{form.group(1)}/identify", {"printing": LOB_1ST, "back": "/" + url.split("/", 3)[3]})
    assert "Confirmed" in body
    assert Store(db).effective(oid).printing_candidates == (LOB_1ST,)


def test_identify_refuses_an_unknown_printing_and_foreign_redirects(ui):
    _, post, _ = ui
    url, body = post("/obs/nope/identify", {"printing": "p:not-real", "back": "//evil.example"})
    assert "unknown printing" in body and "evil" not in url


def test_record_a_sale_on_the_card_page(ui):
    get, post, db = ui
    page = get("/card/89631139")
    assert "Record a sale" in page
    url, body = post("/card/89631139/sale", {"printing": LOB.id, "amount": "950", "currency": "USD", "date": "2026-11-01",
                                             "kind": "transaction", "grade_company": "", "grade": "", "url": ""})
    assert "Sale recorded" in body
    st = Store(db)
    comps, _ = select_comps(st.observations_for(LOB.id, as_of=datetime.now(timezone.utc)), LOB.id, as_of=datetime.now(timezone.utc))
    assert [c.original_amount for c in comps] == [D("950")] and comps[0].evidence_type == ET.INDEPENDENT_TRANSACTION
    for bad, why in (({"amount": "-5"}, "above zero"), ({"date": "2099-01-01"}, "future"), ({"printing": "p:JP_OCG:x"}, "printing of this card"),
                     ({"url": "javascript:alert(1)"}, "http")):
        f = {"printing": LOB.id, "amount": "10", "currency": "USD", "date": "2026-11-01", "kind": "transaction", "url": "", **bad}
        assert why in post("/card/89631139/sale", f)[1]


def test_pages_dropped_in_the_inbox_folder_are_read_and_stored_without_people(tmp_path):
    from tests.test_engine import drop
    e = make(tmp_path)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / "ebay.html").write_bytes((PAGES / "ebay_sold_results.html").read_bytes())
    rep = e.tick(NOW)
    assert rep.new_listings == 4
    blob = b"".join(p.read_bytes() for p in Path(tmp_path / "raw").rglob("*") if p.is_file())
    assert b"cardshop_99" not in blob and b"226000000001" in blob


def test_one_pick_confirms_every_copy_of_the_same_sale(ui):
    get, post, db = ui
    url, body = post("/import/pages", files=[(n, (PAGES / n).read_bytes()) for n in ("ebay_sold_results.html", "130point_results.html")])
    assert body.count(">Blue-Eyes White Dragon LOB-EN001 1st Edition Ultra Rare LP<") == 1 and "also on" in body
    st = Store(db)
    copies = [i for (i,) in st.db.execute("SELECT id FROM observation WHERE listing_id='226000000001'")]
    assert len(copies) == 2
    post(f"/obs/{urllib.parse.quote(copies[0], safe='')}/identify", {"printing": LOB_1ST, "back": "/import"})
    assert all(Store(db).effective(i).printing_candidates == (LOB_1ST,) for i in copies)


def test_japanese_titles_put_japanese_printings_first():
    from cardman.intel import analyze
    from cardman.models import Region
    a = analyze("遊戯王 青眼の白龍 初期 ウルトラレア 美品", CAT)
    top = max(a.identity.candidates, key=lambda p: a.weights[p.id])
    assert top.region == Region.JP_OCG
