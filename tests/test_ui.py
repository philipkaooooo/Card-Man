"""Local review UI: pages render from the store, forms write through the store's own rules."""
import json
import re
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from cardman.slice import run
from cardman.store import Store
from cardman.ui import make_handler
from tests.test_store import NOW, ROOT

DEMO = ROOT / "examples" / "demo_observations_SYNTHETIC.csv"
LATER = NOW + timedelta(hours=1)
TOKEN = "test-token"


def listing(**kw):
    l = json.loads((ROOT / "examples" / "demo_listing_SYNTHETIC.json").read_text())
    l.update(kw)
    return l


@pytest.fixture
def app(tmp_path):
    db = str(tmp_path / "s.sqlite")
    first = run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, TOKEN, clock=lambda: LATER))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path, cookie=None):
        req = urllib.request.Request(base + path)
        if cookie:
            req.add_header("Cookie", cookie)
        with urllib.request.urlopen(req) as r:
            return r.read().decode()

    def post(path, data, token=TOKEN, ctype=None, body=None):
        if body is None:
            body = urlencode({**data, "t": token}).encode()
        req = urllib.request.Request(base + path, data=body, method="POST")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.geturl(), r.read().decode()
        except urllib.error.HTTPError as x:
            return x.code, path, x.read().decode()

    yield type("App", (), {"db": db, "get": staticmethod(get), "post": staticmethod(post),
                           "sid": first["status"]["stored_snapshot_id"]})
    srv.shutdown()


def multipart(fields: dict, filename: str, content: bytes) -> tuple[str, bytes]:
    b = "----cardmantest"
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="csv"; filename="{filename}"\r\n'
                 'Content-Type: text/csv\r\n\r\n'.encode() + content + b"\r\n")
    return f"multipart/form-data; boundary={b}", b"".join(parts) + f"--{b}--\r\n".encode()


def test_deals_and_deal_page_render(app):
    home = app.get("/")                                   # USD by default; the demo FX is 32 TWD per USD
    assert "demo:1" in home and "$500" in home and "pay up to $535" in home and "Buy after checks" in home
    page = app.get(f"/deal/{app.sid}")
    assert "replays exactly" in page and "ACCEPTED_OFFER" in page
    # the call: price works (ask $500 vs pay-up-to $535.16 -> $535 rounded down), authenticity still open
    assert "Good price. Buy once the checks below are done; pay up to $535." in page
    assert "Review the photos for authenticity" in page
    assert "you make $159" in page and "(30%)" in page               # 5,098.40 TWD / 32 at the low-end resale
    assert "Walk away above</small><span>$656" in page                # 21,023 TWD / 32 = 656.97, rounded down
    assert "Break-even resale</small><span>$633" in page              # 20,224.19 TWD / 32 = 632.01, rounded up
    assert "Against each of the last 3 sales: profit in 3, target hit in 3." in page
    assert page.count('<svg class="chart"') == 3         # per-sale profit, price over time, sensitivity
    assert 'class="split zones"' in page and "Hits your target" in page
    assert "Resale 10% lower" in page and "−$56.12" in page          # −1,796 TWD / 32 = 56.125
    assert 'How "pay up to" is worked out' in page and "= pay up to" in page


def test_currency_switch_shows_twd(app):
    page = app.get(f"/deal/{app.sid}", cookie="cur=TWD")
    assert "pay up to NT$17,125" in page and "NT$16,000" in page
    assert app.get("/currency?c=TWD&back=//evil.example").startswith("<!doctype")   # open redirect refused -> "/"


def test_store_values_are_escaped_and_only_web_links_open(app):
    st = Store(app.db)
    from tests.fixtures import obs
    from cardman.models import EvidenceType
    st.add_observation(obs(9, EvidenceType.MARKETPLACE_MARKED_SOLD, "1", source="<script>x</script>",
                           source_url="javascript:alert(1)"))
    page = app.get("/evidence?printing=p%3ALOB-EN001%3AUR%3A1st&syn=1")
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page
    assert 'href="javascript:' not in page


def test_post_without_token_is_refused(app):
    code, _, _ = app.post(f"/deal/{app.sid}/decide", {"action": "NO", "rationale": "x"}, token="wrong")
    assert code == 403 and Store(app.db).decisions(app.sid) == []


def test_decision_recorded_and_wait_needs_trigger(app):
    _, url, page = app.post(f"/deal/{app.sid}/decide", {"action": "WAIT", "rationale": "need photos"})
    assert "WAIT needs a measurable" in page and Store(app.db).decisions(app.sid) == []
    _, _, page = app.post(f"/deal/{app.sid}/decide", {"action": "NO", "rationale": "too thin"})
    assert "Recorded NO" in page and Store(app.db).decisions(app.sid)[0][:2] == ("NO", "too thin")


def test_yes_refused_after_identity_invalidated(app):
    Store(app.db).invalidate_printing("p:LOB-EN001:UR:1st", "wrong print found", now=NOW)
    _, _, page = app.post(f"/deal/{app.sid}/decide", {"action": "YES", "rationale": "go"})
    assert "YES refused" in page and Store(app.db).decisions(app.sid) == []
    assert "identity invalidated" in app.get("/").lower()


def test_status_event_then_reevaluate_makes_a_new_snapshot(app):
    _, _, page = app.post("/obs/d3/status", {"status": "CANCELLED", "reason": "buyer did not pay"})
    assert "Recorded CANCELLED" in page
    assert Store(app.db).original("d3").status == "VALID"
    _, _, page = app.post("/obs/d3/status", {"status": "BOGUS", "reason": "x"})
    assert "unknown status" in page
    _, url, page = app.post(f"/deal/{app.sid}/reevaluate", {})
    assert "Re-evaluated." in page and app.sid not in url
    assert "status CANCELLED: buyer did not pay" in page
    assert "Valuations of this listing" in page                                  # history links both


def test_import_upload(app):
    real = re.sub(r"(?m)^d(\d)", r"r\1", DEMO.read_text().replace("demo://", "https://example.invalid/"))
    ctype, body = multipart({"t": TOKEN}, "sales.csv", real.encode())
    _, url, page = app.post("/import", {}, ctype=ctype, body=body)
    assert url.split("?")[0].endswith("/evidence") and "5 added" in page
    _, _, page = app.post("/import", {}, ctype=ctype, body=body)
    assert "5 already stored" in page
    changed = real.replace("r1,MARKETPLACE_MARKED_SOLD,demo,900", "r1,MARKETPLACE_MARKED_SOLD,demo,990")
    ctype, body = multipart({"t": TOKEN}, "sales.csv", changed.encode())
    _, _, page = app.post("/import", {}, ctype=ctype, body=body)
    assert "1 refused" in page and "r1" in page
    ctype, body = multipart({"t": TOKEN}, "bad.csv", b"id,evidence_type\nx,NOT_A_TYPE\n")
    _, _, page = app.post("/import", {}, ctype=ctype, body=body)
    assert "nothing was imported" in page


def test_evidence_overview_and_script(app):
    page = app.get("/evidence")
    assert "Coverage by printing" in page and "p:LOB-EN001:UR:1st" in page
    page = app.get("/evidence?printing=p%3ALOB-EN001%3AUR%3A1st")
    assert "Synthetic fixture" in page and "Usable sales now" in page       # synthetic refused unless asked
    page = app.get("/evidence?printing=p%3ALOB-EN001%3AUR%3A1st&syn=1")
    assert "Not a sale (asks, bids, estimates)" in page and "Records over time (USD)" in page
    js = app.get("/static/ui.js")
    assert "textContent" in js and "innerHTML" not in js


@pytest.fixture
def app2(tmp_path):
    from tests.fixtures import CATALOG
    db = str(tmp_path / "s.sqlite")
    first = run(listing(), DEMO, NOW, allow_synthetic=True, store_path=db)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, TOKEN, clock=lambda: LATER, catalog=lambda: CATALOG))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return r.read().decode()

    def post(path, data):
        req = urllib.request.Request(base + path, data=urlencode({**data, "t": TOKEN}).encode(), method="POST")
        with urllib.request.urlopen(req) as r:
            return r.geturl(), r.read().decode()

    yield type("App", (), {"get": staticmethod(get), "post": staticmethod(post),
                           "sid": first["status"]["stored_snapshot_id"]})
    srv.shutdown()


def test_left_menu_and_currency_next_to_logo(app2):
    h = app2.get("/")
    assert h.index('class="logo"') < h.index('class="curs"') < h.index("<aside>")
    for path in ("/scan", "/results", "/evidence", "/import"):
        assert f'href="{path}"' in h
    assert 'aria-current="page">Deals' in h


def test_scan_page(app2):
    h = app2.get("/scan")
    assert "Paste any listing" in h and "Every printing" not in h
    h = app2.get("/scan?" + urlencode({"title": "Blue-Eyes White Dragon LOB-EN001 Ultra Rare 1st Edition",
                                       "ask": "480", "cur": "USD", "syn": "1"}))
    assert "Every printing it could be" in h and "LOB-EN001" in h
    assert "Edition claim (1st) is unverified" not in h      # fixture catalog documents editions
    h = app2.get("/scan?" + urlencode({"title": "Blue-Eyes White Dragon orica", "ask": "5"}))
    assert "Not a genuine in-scope single" in h
    h = app2.get("/scan?" + urlencode({"title": "Blue-Eyes", "ask": "abc"}))
    assert "is not a number" in h


def test_results_and_recording_outcomes(app2):
    h = app2.get("/results?syn=1")
    assert "CardMan vs a price tracker" in h and "Record what happened" in h
    url, h = app2.post("/outcome", {"position": "BEWD #1", "kind": "PURCHASE", "at": "2026-11-04",
                                    "amount": "480", "currency": "USD", "snapshot_id": app2.sid})
    assert "Recorded: BEWD #1" in h and "BEWD #1" in h and "Holding" in h
    url, h = app2.post("/outcome", {"position": "BEWD #1", "kind": "NOPE"})
    assert "Not recorded" in h
    url, h = app2.post("/outcome", {"position": "x", "kind": "PURCHASE", "amount": "1", "currency": "USD",
                                    "snapshot_id": "missing"})
    assert "Not recorded" in h
