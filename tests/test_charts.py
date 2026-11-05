"""SVG chart helpers: labels never repeat or escape, overlapping marks stay visible."""
from datetime import datetime, timedelta, timezone

from cardman import charts
from cardman.charts import Point, Ref

T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_time_ticks_never_repeat_a_label():
    for span in (timedelta(hours=5), timedelta(days=2), timedelta(days=35), timedelta(days=800)):
        labels = [l for _, l in charts.time_ticks(T, T + span)]
        assert labels and len(labels) == len(set(labels)) and len(labels) <= 8


def test_tooltips_and_labels_are_escaped():
    p = Point(T, 100.0, "MARKETPLACE_MARKED_SOLD", "used", '<img src=x onerror="1">')
    svg = charts.price_chart([p], [Ref("<b>Ask</b>", 90.0, "ask")], currency="TWD", valued_at=T + timedelta(days=1))
    assert "<img" not in svg and "<b>" not in svg and "&lt;img" in svg


def test_identical_points_are_stepped_apart_and_close_ref_labels_do_not_overlap():
    pts = [Point(T, 100.0, "MARKETPLACE_MARKED_SOLD", "used", "a"),
           Point(T, 100.0, "AGGREGATE_ESTIMATE", "unused", "b")]
    refs = [Ref("Ask", 100.0, "ask"), Ref("Buy ceiling", 100.5, "ceiling")]
    charts.price_chart(pts, refs, currency="TWD", valued_at=T + timedelta(days=3))
    assert sorted(p._dx for p in pts) == [0, 9]
    svg = charts.price_chart([], refs, currency="TWD", valued_at=T)
    ys = [float(chunk.split('"')[0]) for chunk in svg.split('class="rl')[1:] for chunk in [chunk.split(' y="')[1]]]
    assert abs(ys[0] - ys[1]) >= 15


def test_bar_labels_fit_inside_the_chart():
    rows = [("Resale 10% lower", -1796.0, "now NT$15,329; ask too high"), ("Held 3 months longer", -425.0, "")]
    svg = charts.hbars(rows, "TWD", width=560)
    import re
    width = 560
    for x, anchor, text in re.findall(r'<text class="rl strong" x="([\d.]+)" y="[\d.]+" text-anchor="(\w+)">([^<]*)<', svg):
        end = float(x) + (len(text) * 6.8 if anchor == "start" else 0)
        assert end <= width + 1, (text, end)
    assert "−NT$1,796 · now NT$15,329; ask too high" in svg
