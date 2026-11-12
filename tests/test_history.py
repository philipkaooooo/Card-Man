"""Price history: buckets, gaps, trend significance, no pooling, the ask series, the chart endpoint. Made-up data only."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import history as h
from cardman.models import EvidenceType as ET
from cardman.store import Store
from tests.fixtures import obs

UTC = timezone.utc
NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
JST = timezone(timedelta(hours=9))


def test_weeks_are_iso_monday_weeks_in_utc_and_months_are_calendar_months():
    sun_late = datetime(2026, 11, 8, 23, 30, tzinfo=UTC)                 # Sunday
    assert h.week_start(sun_late).isoformat() == "2026-11-02"
    assert h.week_start(datetime(2026, 11, 9, 0, 0, tzinfo=UTC)).isoformat() == "2026-11-09"
    # Monday 01:00 in Tokyo is still Sunday in UTC: the same instant always lands in the same bucket.
    assert h.week_start(datetime(2026, 11, 9, 1, 0, tzinfo=JST)).isoformat() == "2026-11-02"
    assert h.month_start(datetime(2026, 12, 1, 3, 0, tzinfo=JST)).isoformat() == "2026-11-01"
    assert h.week_start(datetime(2027, 1, 1, tzinfo=UTC)).isoformat() == "2026-12-28"   # ISO week 53 of 2026


def test_bucket_numbers_and_band_only_with_three_sales():
    pts = [(datetime(2026, 11, 3, tzinfo=UTC), v) for v in (10, 20, 30, 40)] + [(datetime(2026, 11, 10, tzinfo=UTC), v) for v in (50, 70)]
    b = h.buckets(pts, "week")
    assert [x["start"] for x in b] == ["2026-11-02", "2026-11-09"]
    assert b[0]["n"] == 4 and b[0]["median"] == 25 and b[0]["p25"] == 17.5 and b[0]["p75"] == 32.5
    assert b[1]["median"] == 60 and b[1]["p25"] is None and b[1]["p75"] is None


def test_empty_weeks_are_gaps_not_filled_in():
    pts = [(datetime(2026, 9, 1, tzinfo=UTC), 10.0), (datetime(2026, 9, 2, tzinfo=UTC), 11.0),
           (datetime(2026, 10, 20, tzinfo=UTC), 30.0), (datetime(2026, 10, 21, tzinfo=UTC), 31.0)]
    b = h.buckets(pts, "week")
    assert len(b) == 2                                   # nothing invented for the six empty weeks between
    svg = h.sparkline(b, datetime(2026, 11, 12, tzinfo=UTC))
    assert svg.count("<circle") == 2 and "<polyline" not in svg   # two separate points, no line drawn across the gap


def test_trend_is_labelled_only_when_the_interval_excludes_zero():
    falling = [(NOW - timedelta(days=d), 100 * 0.99 ** (90 - d) * (1 + 0.02 * ((d * 7) % 3 - 1))) for d in range(0, 91, 7)]
    t = h.trend(falling)
    # d days ago the price is 100 * 0.99^(90-d) with a little noise: newer sales are lower, so the trend falls.
    assert t["label"] == "falling" and t["ci"][1] < 0
    noisy = [(NOW - timedelta(days=d), v) for d, v in zip(range(0, 91, 7), (100, 140, 90, 130, 95, 135, 100, 120, 92, 138, 99, 125, 101, 131))]
    assert h.trend(noisy)["label"] == "no clear trend"
    assert h.trend(falling[:4])["label"] == "no clear trend" and "needs 6+" in h.trend(falling[:4])["why"]
    short = [(NOW - timedelta(days=d), 100.0 - d) for d in range(8)]
    assert h.trend(short)["label"] == "no clear trend"


def test_ninety_day_change_needs_three_sales_at_each_end():
    recent = [(NOW - timedelta(days=d), 120.0) for d in (1, 5, 9)]
    then = [(NOW - timedelta(days=d), 100.0) for d in (95, 100, 110)]
    assert round(h.change_90(recent + then, NOW)["pct"], 6) == 20.0
    assert h.change_90(recent + then[:2], NOW)["pct"] is None


def _sale(st, i, amount, days, printing, **kw):
    when = kw.pop("when", NOW - timedelta(days=days))
    o = obs(i, kw.pop("et", ET.INDEPENDENT_TRANSACTION), str(amount), printing=printing, when=when, capture_time=NOW,
            currency=kw.pop("currency", "USD"), synthetic_fixture=False, source=kw.pop("source", "manual"), **kw)
    st.add_observation(o, now=NOW)


class V:
    def __init__(self, id, set_code="LOB-EN001", rarity="Ultra Rare", edition=None):
        self.id, self.set_code, self.rarity, self.edition = id, set_code, rarity, edition


def test_series_never_pool_printings_or_grades_and_undated_sales_are_counted_not_charted(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    a, b = "p:A@1|ed:1st", "p:A@1|ed:Unlimited"
    for i, d in enumerate((3, 10, 20)):
        _sale(st, f"a{i}", 100, d, a)
        _sale(st, f"b{i}", 30, d, b)
        _sale(st, f"g{i}", 900, d, a, grade_company="PSA", grade="10")
    _sale(st, "m", 55, 0, b, source="mercari_jp", et=ET.MARKETPLACE_MARKED_SOLD, when=NOW)   # Mercari: no sale date
    conv = h.Converter(st, "TWD", "USD")
    out = h.build(st, [V(a, edition="1st"), V(b, edition="Unlimited")], NOW, conv)
    by = {(s["printing_id"], s["grade"]): s for s in out["series"]}
    assert set(by) == {(a, "Raw"), (b, "Raw"), (a, "PSA 10")}
    assert {d["p"] for d in by[(a, "Raw")]["sales"]} == {100.0}
    assert {d["p"] for d in by[(b, "Raw")]["sales"]} == {30.0}
    assert {d["p"] for d in by[(a, "PSA 10")]["sales"]} == {900.0}
    assert out["undated"] == 1 and len(by[(b, "Raw")]["sales"]) == 3
    assert out["depth"] == {"You": (NOW - timedelta(days=20)).date().isoformat()}


def test_ask_daily_keeps_one_row_per_printing_per_day_and_past_days_stay(tmp_path):
    from cardman.card_market import ensure_index
    from tests.test_card_market import _store
    rows = [("a", "ebay_browse", [("p:A@1", 1.0)], 30, {}), ("b", "ebay_browse", [("p:A@1", 1.0)], 50, {}),
            ("c", "ebay_browse", [("p:A@1", 0.5), ("p:B@1", 0.5)], 5, {})]          # not sure of the printing: left out
    st = _store(tmp_path, rows)
    st.db.execute("ALTER TABLE listing ADD COLUMN first_seen TEXT")
    st.db.execute("ALTER TABLE listing ADD COLUMN last_seen TEXT")
    with st.db:
        st.db.execute("UPDATE listing SET price = CASE key WHEN 'a' THEN '20' ELSE '40' END, currency='TWD', "
                      "first_seen='2026-11-10T00:00:00+00:00', last_seen='2026-11-12T00:00:00+00:00'")
    ensure_index(st)
    h.update_ask_daily(st, NOW, "TWD")
    got = st.db.execute("SELECT printing_id, day, low, median, n FROM ask_daily ORDER BY day").fetchall()
    assert got == [("p:A@1", d, 20.0, 30.0, 2) for d in ("2026-11-10", "2026-11-11", "2026-11-12")]
    with st.db:
        st.db.execute("UPDATE listing SET price='1' WHERE key='a'")
    h.update_ask_daily(st, NOW, "TWD")
    got = dict(((d, low) for _, d, low, _, _ in st.db.execute("SELECT printing_id, day, low, median, n FROM ask_daily")))
    assert got == {"2026-11-10": 20.0, "2026-11-11": 20.0, "2026-11-12": 1.0}    # only today is rewritten


def test_chart_endpoint_on_fixtures(tmp_path, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from cardman.cardinfo import CardIndex, ImageCache
    from cardman.ui import make_handler
    from tests.real_catalog import DATA
    from tests.test_sold_import import CAT, LOB
    db = str(tmp_path / "s.sqlite")
    st = Store(db)
    from cardman.identity import edition_variants
    pid = next(v.id for v in edition_variants(LOB, CAT) if v.id.endswith("|ed:1st"))
    for i, (d, p) in enumerate(((2, 950), (9, 900), (16, 1000), (40, 800))):
        _sale(st, f"s{i}", p, d, pid)
    st.db.close()
    cards = CardIndex(cards_dir=DATA, db_path=tmp_path / "ci.sqlite", catalog=CAT)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, "tok", clock=lambda: NOW, catalog=lambda: CAT, cards=cards,
                                                             images=ImageCache(tmp_path / "img", get=lambda u: (_ for _ in ()).throw(OSError()))))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        get = lambda p: urllib.request.urlopen(urllib.request.Request(base + p, headers={"Cookie": "cur=USD"})).read().decode()
        d = json.loads(get("/card/89631139/history.json"))
        s = next(x for x in d["series"] if x["key"] == d["default"])
        assert s["printing_id"] == pid and s["grade"] == "Raw" and len(s["sales"]) == 4
        assert [(b["start"], b["n"], b["median"]) for b in s["month"]] == [("2026-10-01", 2, 900), ("2026-11-01", 2, 925)]
        assert s["summary"]["n_30"] == 3 and s["summary"]["median_30"] == 950 and s["summary"]["change_90"]["pct"] is None
        page = get("/card/89631139")
        assert 'id="history"' in page and 'data-hchart="/card/89631139/history.json"' in page and "Backfill 3 months" in page
        assert "Data table" in page and "30-day median" in page
        frag = get(f"/card/89631139/history.html?key={urllib.parse.quote(d['default'])}")
        assert "data-hchips" in frag and "Every sale" in frag
        assert "CHART" not in get("/static/ui.js") and "data-hchart" in get("/static/ui.js")
    finally:
        srv.shutdown()
