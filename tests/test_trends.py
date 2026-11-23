"""The materialized market board: medians, trends, asks, and no invented data."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import trends
from cardman.history import ASK_SCHEMA
from cardman.models import EvidenceType as ET, MarketObservation
from cardman.store import Store

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
P = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes:ultra-rare@89631139"


def add(st, i, amount, days, *, printing=P, condition=None):
    when = NOW - timedelta(days=days)
    st.add_observation(MarketObservation(
        id=f"t{i}", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="fixture", original_amount=D(str(amount)), currency="TWD",
        event_time=when, capture_time=when, source_url=f"fixture://{i}", printing_candidates=(printing,),
        observed_condition=condition), now=NOW)


def test_rising_series_and_a_thin_series_are_reported_honestly(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i, price in enumerate((100, 110, 120, 130, 140, 150, 160)):
        add(st, i, price, 120 - i * 18)
    thin = "p:EN_TCG:LOB-EN005:dark-magician:ultra-rare@46986414"
    add(st, "a", 50, 10, printing=thin)
    add(st, "b", 55, 5, printing=thin)
    assert trends.refresh(st, NOW, "TWD") == 2
    board = {r["printing_id"]: r for r in trends.board(st, game="ygo", limit=10)}
    rising = board[P]
    assert rising["n_sales"] == 7 and rising["trend_label"] == "rising"
    assert rising["plain"].startswith("Up")
    assert rising["label"].startswith("LOB-EN001")
    assert board[thin]["trend_label"] == "no clear trend" and board[thin]["plain"] is None
    assert [r["printing_id"] for r in trends.board(st, game="ygo", min_sales=3)] == [P]
    s = trends.summary(st, "ygo")
    assert (s["series"], s["rising"], s["falling"]) == (2, 1, 0)


def test_live_asks_stay_a_separate_series(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i in range(3):
        add(st, i, 100, i + 1)
    st.db.executescript(ASK_SCHEMA)
    with st.db:
        st.db.execute("INSERT INTO ask_daily VALUES (?,?,?,?,?,?,?)", (P, "|", NOW.date().isoformat(), 150.0, 160.0, 4, "TWD"))
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert r["ask_low"] == 150.0 and abs(r["spread"] - 50.0) < 1e-9      # the cheapest ask is the one compared
    assert r["median"] == 100.0


def test_only_the_newest_snapshot_is_live_and_a_stale_one_is_not(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i in range(3):
        add(st, i, 100, i + 1)
    st.db.executescript(ASK_SCHEMA)
    old, new = (NOW - timedelta(days=5)).date().isoformat(), NOW.date().isoformat()
    with st.db:
        st.db.execute("INSERT INTO ask_daily VALUES (?,?,?,?,?,?,?)", (P, "|", old, 90.0, 95.0, 3, "TWD"))
        st.db.execute("INSERT INTO ask_daily VALUES (?,?,?,?,?,?,?)", (P, "|", new, 210.0, 220.0, 2, "TWD"))
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert r["ask_low"] == 210.0 and r["ask_day"] == new          # yesterday's dead listing is not today's price
    with st.db:
        st.db.execute("DELETE FROM ask_daily WHERE day=?", (new,))
    trends.refresh(st, NOW, "TWD")
    assert trends.board(st, game="ygo", limit=1)[0]["ask_median"] is None   # nothing seen in the last three days


def test_market_page_carries_both_games_and_a_column_header_instead_of_a_legend(tmp_path):
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    for i in range(3):
        add(st, i, 90 + i * 10, i + 1)
    trends.refresh(st, NOW, "TWD")
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert 'role="tab"' in h and "Yu-Gi-Oh!" in h and "Pokémon" in h      # both games reachable from either board
    assert ('class="mhead"' in h and "Typical sold" in h and "Sales pace" in h and "Cheapest ask" in h
            and "Trend" in h)
    assert "Sells for" not in h                                          # the column names the job; no per-row caption
    assert "How to read a row" not in h                                  # the header strip does that job now
    p = view_market(st, NOW, "TWD", "TWD", {}, game="pkm")
    assert "The Pokémon market" in p and 'role="tab"' in p


def test_newest_sales_against_the_ones_before_them(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i in range(2):
        add(st, i, 100, 45 + i)          # the two older sales
    for i in range(2):
        add(st, f"r{i}", 120, 4 + i)     # the two newest sales
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert abs(r["move_recent"] - 20.0) < 1e-9 and r["move_recent_label"] == "rising"
    assert (r["move_n"], r["move_past"]) == (2, 2)


def test_raw_sales_restate_to_near_mint(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    add(st, 0, 78, 2, condition="LP")
    add(st, 1, 100, 3)
    add(st, 2, 100, 4)
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert abs(r["median"] - 100.0) < 1e-6       # 78 in Lightly Played is 100 in Near Mint on the ladder


def test_board_trend_cell_names_the_direction_and_its_evidence(tmp_path):
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    for i in range(2):
        add(st, i, 100, 45 + i)
    for i in range(2):
        add(st, f"r{i}", 120, 4 + i)
    trends.refresh(st, NOW, "TWD")
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert 'class="mtrend rising"' in h and "Climbing" in h and "+20%" in h
    cell = h.split('class="mtrend rising"', 1)[1].split("</span></div></li>", 1)[0]
    assert "30d" not in cell                                        # the cell cites sales counts, not a day window
    assert "newest 2 vs 2 before" in h
    assert 'class="mkph-bar"' in h and 'class="fbar"' in h and 'class="flab">Order<' in h
    assert 'class="mtile up"' in h and 'class="mtile down"' in h and '>Climbing<' in h and '>Slipping<' in h
    assert "Climbing: up 5% or more" in h                           # the rule sits in the bar's tooltip, not a paragraph
    assert "Every row uses the same rule" not in h
    assert 'href="/market?label=rising"' in h


def test_thin_sales_take_pricechartings_year_as_the_call(tmp_path):
    """Two sales cannot make a trend; PriceCharting's weekly series for the same grade can, and it says so."""
    import json
    from cardman.pc import SCHEMA as PC_SCHEMA
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    add(st, 0, 100, 30)
    add(st, 1, 100, 20)
    st.db.executescript(PC_SCHEMA)
    weeks = [[1740812400000, 10.0], [1760000000000, 11.0], [1770000000000, 12.0], [1780000000000, 18.0]]
    with st.db:
        st.db.execute(
            "INSERT INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, pop, fetched, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (P, "ygo", "https://www.pricecharting.com/game/yugioh/blue-eyes", "Blue-Eyes LOB-EN001", "q", "test",
             json.dumps({"Ungraded": {"p": 18.0, "c": 0.0, "vol": "1 sale per week"}}),
             json.dumps({"Ungraded": weeks}), json.dumps({}), NOW.isoformat(), "ok"))
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert r["move_recent"] is None and r["pc_move_label"] == "rising" and abs(r["pc_move"] - 80.0) < 1e-9
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert 'class="mtrend rising"' in h and "Climbing" in h and "+80%" in h and "2 sales · PC 12 mo" in h


def test_board_without_pricecharting_stays_thin(tmp_path):
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    add(st, 0, 100, 30)
    add(st, 1, 100, 20)
    trends.refresh(st, NOW, "TWD")
    r = trends.board(st, game="ygo", limit=1)[0]
    assert r["pc_move"] is None and r["move_recent"] is None
    assert "Thin" in view_market(st, NOW, "TWD", "TWD", {}, game="ygo")


def test_no_sales_no_row(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    assert trends.refresh(st, NOW, "TWD") == 0
    assert trends.board(st, game="ygo") == []
