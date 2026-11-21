"""The mark switches with the card. Numbers here are fixtures, not a live book."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman.read import Point, read_points

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
ago = lambda d: NOW - timedelta(days=d)


def sold(source, amount, days, exact=True, note=""):
    return Point(ago(days), D(str(amount)), source, "sold", exact, note)


def test_thick_falling_window_marks_the_recent_median_not_the_old_highs():
    rows = [sold("shop", p, d) for d, p in ((40, 100), (38, 102), (35, 98), (32, 100))]
    rows += [sold("shop", p, d) for d, p in ((12, 50), (10, 52), (8, 48), (6, 51), (4, 49), (3, 50), (2, 47), (1, 53))]
    rows.append(Point(NOW, D("140"), "index", "index", True, "lagging"))
    read = read_points(rows, NOW)
    assert read.mark == D("50")
    assert "falling" in read.flags and "index-lag" in read.flags
    assert "thin" not in read.flags
    assert read.mark != D("140")


def test_few_falling_sales_mark_the_low_of_the_last_three():
    rows = [sold("shop", 100, d) for d in (40, 36, 32, 28)]
    rows += [sold("shop", p, d) for d, p in ((4, 50), (3, 52), (2, 48), (1, 51))]
    rows.append(Point(NOW, D("80"), "shop", "ask"))
    read = read_points(rows, NOW)
    assert read.mark == D("48")
    assert read.stress == D("48")


def test_a_live_ask_under_the_model_replaces_it():
    rows = [sold("shop", p, d) for d, p in ((8, 98), (7, 99), (6, 100), (5, 100), (4, 101), (3, 102), (2, 103))]
    rows.append(Point(NOW, D("70"), "shop", "ask"))
    read = read_points(rows, NOW)
    assert read.mark == D("70")
    assert "ask-undercut" in read.flags


def test_one_odd_print_cannot_veto_a_deep_source():
    rows = [sold("ledger", p, d) for d, p in (
        (13, 100), (12, 102), (11, 98), (10, 101), (9, 99), (8, 100), (7, 103), (6, 97), (4, 100),
    )]
    rows.append(sold("aggregator", 70, 1))
    read = read_points(rows, NOW)
    assert "contested" not in read.flags
    assert read.mark is not None and read.mark >= D("97")
    assert read.stress == D("70")


def test_a_near_twin_across_sources_is_one_sale():
    rows = [sold("ledger", p, d) for d, p in ((10, 100), (9, 101), (8, 99), (7, 100), (6, 102), (5, 98), (3, 100))]
    rows.append(sold("ledger", "100.00", 2))
    rows.append(sold("aggregator", "100.05", 1))
    read = read_points(rows, NOW)
    assert "contested" not in read.flags
    assert read.mark == D("100")


def test_mixed_listing_count_is_not_added():
    rows = [
        Point(NOW, D(4), "shop", "listing", True, "this printing"),
        Point(NOW, D(100), "other", "listing", False, "three colors together"),
        Point(NOW, D(0), "blocked", "unavailable", False, ""),
    ]
    read = read_points(rows, NOW)
    assert read.listings == 4
    assert read.held[0].listings == 100
    assert read.unavailable == ("blocked",)


def test_one_fresh_sale_under_the_prior_month_is_the_mark():
    rows = [sold("shop", 100, d) for d in (40, 30, 20)]
    rows.append(sold("shop", 40, 2))
    read = read_points(rows, NOW)
    assert read.mark == D("40")
    assert "falling" in read.flags and "thin" in read.flags


def test_nothing_in_45_days_sets_no_price():
    from cardman.economics import scenarios_from_comps
    from cardman.read import apply_read
    rows = [sold("shop", p, d) for d, p in ((80, 100), (70, 110), (60, 90), (50, 105))]
    read = read_points(rows, NOW)
    assert read.mark is None and "stale" in read.flags
    out = apply_read(read, scenarios_from_comps([D(100), D(110), D(90), D(105)]))
    assert out.conservative is None and out.method == "read"


def test_a_small_settled_sample_keeps_its_own_exit():
    from cardman.economics import scenarios_from_comps
    from cardman.read import apply_read
    rows = [sold("shop", p, d) for d, p in ((10, 100), (6, 110), (1, 90))]
    read = read_points(rows, NOW)
    base = scenarios_from_comps([D(100), D(110), D(90)])
    out = apply_read(read, base)
    assert read.mark is None and "stale" not in read.flags
    assert out.method == base.method and out.conservative == base.conservative
