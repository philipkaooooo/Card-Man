"""The main pages speak plainly: none of the system's own words reach a collector.

Checked: everything a person can read on the page, including tooltips, button text, labels and placeholders. Not
checked: link targets, form field names and values, and scripts and styles, which a person never reads."""
import re
import threading
import urllib.parse
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

from cardman.cardinfo import CardIndex, ImageCache
from cardman.models import EvidenceType as ET
from cardman.store import Store
from cardman.ui import make_handler
from tests.fixtures import obs
from tests.real_catalog import DATA
from tests.test_sold_import import CAT, LOB

BANNED = ["median", "quantile", "evidence", "scenario", "passcode", "MARKETPLACE_", "CANDIDATES", "n=", "P25", "backfill"]
from tests.test_store import NOW


def readable(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    page = re.sub(r'\s(?:href|src|action|name|value|class|id|for|type|data-(?!tip)[a-z-]+)="[^"]*"', " ", page)
    return page


def _opps(st):
    import json
    from tests.test_listing_verdict import D, sc
    st.db.executescript(
        "CREATE TABLE IF NOT EXISTS opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
        "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE IF NOT EXISTS listing (key TEXT PRIMARY KEY, source TEXT, listing_id TEXT, market TEXT, title TEXT, url TEXT, seller TEXT, "
        "kind TEXT, price TEXT, currency TEXT, ends_at TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, fingerprint TEXT, data_json TEXT);")
    pid = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes-white-dragon:ultra-rare@89631139"
    rows = {
        "buy": D(kinds=["UNDERPRICED"], printing_id=pid, scenarios=[sc(pid=pid)], all_in_home=300.0, priced_share=1.0),
        "check": D(kinds=["RIGHT_PRINT_ONLY"], scenarios=[sc(pid=pid, weight=0.5), sc(pid=pid + "|ed:Unlimited", weight=0.5, basis="none")],
                   all_in_home=300.0, priced_share=0.5),
        "misid": D(kinds=["MISIDENTIFIED"], findings=[{"kind": "CODE_NAME_MISMATCH", "severity": "WARN", "text": "LOB-EN005 belongs to Dark Magician"}],
                   scenarios=[sc(pid=pid, basis="none")]),
        "guide": D(kinds=["REFERENCE_GAP"], scenarios=[sc(pid=pid, basis="reference")]),
        "lot": D(kinds=["LOT", "COLLECTION"], title="Yugioh lot of 250 cards", scenarios=[]),
    }
    with st.db:
        for k, d in rows.items():
            key = f"ebay_browse:EBAY_US:{k}"
            d.setdefault("title", f"Blue-Eyes White Dragon LOB-EN001 {k}")
            st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key, "SPECTATE", "OPEN", 1.0, None, 0.0, json.dumps(d.get("kinds")), d.get("printing_id"), None, "fp",
                           json.dumps(d), "t", "t"))
            st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (key, "ebay_browse:EBAY_US", k, "INTL", d["title"], "https://www.ebay.com/itm/1", None, "ask", "250", "USD", None,
                           "2026-11-10T00:00:00+00:00", "2026-11-12T00:00:00+00:00", None, "f", "{}"))
    return "ebay_browse:EBAY_US:check"


def test_main_pages_have_no_system_words(tmp_path, monkeypatch):
    from cardman.identity import edition_variants
    monkeypatch.setattr("cardman.engine.ROOT", tmp_path)
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    pid = next(v.id for v in edition_variants(LOB, CAT) if v.id.endswith("|ed:1st"))
    for i, (d, p) in enumerate(((2, 950), (9, 900), (16, 1000), (40, 800))):
        when = NOW - timedelta(days=d)
        st.add_observation(obs(f"s{i}", ET.INDEPENDENT_TRANSACTION, str(p), printing=pid, when=when, capture_time=NOW, currency="USD",
                               synthetic_fixture=False, source="manual"), now=NOW)
    key = _opps(st)
    st.db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", clock=lambda: NOW, catalog=lambda: CAT, cards=cards,
                                                             images=ImageCache(tmp_path / "img", get=lambda u: (_ for _ in ()).throw(OSError()))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    pages = ["/", "/opportunities", "/opportunities?v=every", "/search?q=blue-eyes", "/card/89631139",
             "/opp/" + urllib.parse.quote(key, safe=""), "/opp/" + urllib.parse.quote("ebay_browse:EBAY_US:misid", safe="")]
    try:
        for p in pages:
            page = urllib.request.urlopen(base + p).read().decode()
            assert "<!doctype" in page
            text = readable(page)
            found = [w for w in BANNED if re.search(re.escape(w), text, re.I)]
            assert not found, f"{p} still says {found}: " + "; ".join(
                text[max(0, m.start() - 60):m.end() + 30] for w in found for m in [re.search(re.escape(w), text, re.I)])
        assert "Check first" in urllib.request.urlopen(base + "/opportunities").read().decode()
    finally:
        srv.shutdown()
