"""The PriceCharting cross-market: captured pages parse exactly, and the board shows them without inventing picks."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import pc, trends
from cardman.fx import FxRate
from cardman.models import EvidenceType as ET, MarketObservation
from cardman.store import Store

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
P = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes:ultra-rare@89631139"

PD = ("Ungraded\tGrade 7\tGrade 8\t\n \n\n$2.38 +$0.22\t$17.95 -$0.01\t$21.40 -$0.01\t\n \n\n"
      "1 sale per day\t5 sales per year\t1 sale per month\t\n+ \n\n \t \t \t\n \n\n"
      "Grade 9\tGrade 9.5\tPSA 10\t\n \n\n$24.00 +$1.69\t$26.00 +$1.00\t$141.25 +$6.25\t\n \n\n"
      "1 sale per week\t4 sales per year\t2 sales per week\t\n")

SERIES = {
    "Ungraded": [[1740812400000, 3.0], [1754028000000, 1.95], [1780293600000, 2.0], [1793512800000, 2.38]],
    "7": [[1740812400000, 0.0], [1754028000000, 9.99], [1780293600000, 14.54], [1793512800000, 17.95]],
    "8": [[1740812400000, 0.0], [1754028000000, 11.13], [1780293600000, 15.0], [1793512800000, 21.4]],
    "9": [[1740812400000, 12.56], [1754028000000, 11.61], [1780293600000, 20.79], [1793512800000, 24.0]],
    "BGS 9.5": [[1740812400000, 14.0], [1754028000000, 14.38], [1780293600000, 27.75], [1793512800000, 26.0]],
    "PSA 10": [[1740812400000, 39.99], [1754028000000, 60.61], [1780293600000, 87.5], [1793512800000, 141.25]],
}


def payload(url="https://www.pricecharting.com/game/pokemon-journey-together/mimikyu-ex-69"):
    return {"title": "Mimikyu ex #69 Prices | Pokemon Journey Together | Pokemon Cards", "url": url,
            "product": {"id": 8871273}, "pd": PD, "series": SERIES,
            "pop": {"psa": [0, 1, 0, 3, 0, 5, 16, 70, 259, 124]}}


def store_row(st, pid, row, game="pkm"):
    from cardman.pc import SCHEMA
    st.db.executescript(SCHEMA)
    with st.db:
        st.db.execute(
            "INSERT OR REPLACE INTO pc_card (printing_id, game, url, title, query, match_note, grades, series, pop, "
            "fetched, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, game, row.get("url"), row.get("title"), "q", "test", json.dumps(row["grades"]),
             json.dumps(row["series"]), json.dumps(row["pop"]), NOW.isoformat(), "ok"))


def test_grade_cells_parse_in_display_order():
    g = pc.parse_price_data(PD)
    assert list(g) == ["Ungraded", "Grade 7", "Grade 8", "Grade 9", "Grade 9.5", "PSA 10"]
    assert g["Ungraded"] == {"p": 2.38, "c": 0.22, "vol": "1 sale per day"}
    assert g["PSA 10"] == {"p": 141.25, "c": 6.25, "vol": "2 sales per week"}
    assert g["Grade 8"]["c"] == -0.01


def test_zero_change_cells_do_not_shift_the_prices():
    """PriceCharting prints an unchanged grade as `$1,922.70 $0.00`; that second dollar is a change, not a price."""
    cells = {"Ungraded": "$1.04 -$0.08", "Grade 7": "$287.10 -$0.05", "Grade 8": "$437.60 -$0.08",
             "Grade 9": "$1,922.70\n     \n     $0.00", "Grade 9.5": "$2,115.00\n   $0.00",
             "PSA 10": "$2,307.00\n   $0.00"}
    vols = ["1 sale per day", "rare", "rare", "volume: \n 1 sale per week", "volume: \n rare", "volume: \n rare"]
    g = pc.cells_to_grades(cells, vols)
    assert g["Grade 9"] == {"p": 1922.7, "c": 0.0, "vol": "1 sale per week"}
    assert g["PSA 10"] == {"p": 2307.0, "c": 0.0, "vol": "rare"}
    assert g["Ungraded"]["c"] == -0.08
    assert g["Grade 7"]["vol"] == "rare"


def test_series_are_named_like_the_grade_strip_and_keep_a_year():
    row = pc.parse_product(payload())
    assert row["grades"]["PSA 10"]["p"] == 141.25
    assert set(row["series"]) == {"Ungraded", "Grade 7", "Grade 8", "Grade 9", "BGS 9.5", "PSA 10"}
    assert row["series"]["PSA 10"][-1] == [1793512800000, 141.25]
    assert row["series"]["Grade 7"][0][1] == 0.0          # a zero week is kept: the gap is the story


def test_grade_series_matches_our_grade_keys():
    row = pc.parse_product(payload())
    assert pc.grade_series(row, "|")[0] == "Ungraded"
    assert pc.grade_series(row, "PSA|10")[0] == "PSA 10"
    assert pc.grade_series(row, "BGS|9.5")[0] == "BGS 9.5"
    assert pc.grade_series(row, "BGS|7")[0] == "Grade 7"
    assert pc.grade_series(row, "CGC|10") is None         # no CGC 10 series: say nothing rather than the wrong line


def test_pick_link_prefers_the_exact_number():
    links = [{"h": "/game/pokemon-journey-together/mimikyu-ex-67", "t": "Mimikyu ex #67"},
             {"h": "/game/pokemon-journey-together/mimikyu-ex-69", "t": "Mimikyu ex #69"},
             {"h": "/game/pokemon-journey-together/mimikyu-ex-69-jumbo", "t": "Jumbo"}]
    url, why = pc.pick_link(links, name="Mimikyu ex Journey Together", number="69")
    assert url == "/game/pokemon-journey-together/mimikyu-ex-69" and why


def test_pick_link_keeps_the_rarity_we_track():
    links = [{"h": "/game/yugioh-magnificent-maestros/the-guiding-flute-starlight-rare-mams-en003", "t": ""},
             {"h": "/game/yugioh-magnificent-maestros/the-guiding-flute-starlight-rare-mams-en003",
              "t": "The Guiding Flute of Summoning Dragons [Starlight Rare] MAMS-EN003"},
             {"h": "/game/yugioh-magnificent-maestros/the-guiding-flute-mams-en003",
              "t": "The Guiding Flute of Summoning Dragons MAMS-EN003"},
             {"h": "/game/yugioh-magnificent-maestros/the-guiding-flute-grand-master-rare-mams-en003",
              "t": "The Guiding Flute of Summoning Dragons [Grand Master Rare] MAMS-EN003"}]
    url, _ = pc.pick_link(links, code="MAMS-EN003", rarity="ultra rare")
    assert url.endswith("/the-guiding-flute-mams-en003")
    url, _ = pc.pick_link(links, code="MAMS-EN003", rarity="starlight rare")
    assert url.endswith("/the-guiding-flute-starlight-rare-mams-en003")


def test_sweep_skips_fresh_rows_and_ingest_parses_captures(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    store_row(st, "pk:EN:sv9-69:holo@pkm-sv9-69", pc.parse_product(payload()))
    cap = tmp_path / "cap.json"
    cap.write_text(json.dumps([{"printing_id": "pk:EN:sv10-1:normal@pkm-sv10-1", "payload": payload()}]))
    assert pc.ingest(st, str(cap)) == 1
    assert set(pc.rows_for(st, ["pk:EN:sv9-69:holo@pkm-sv9-69", "pk:EN:sv10-1:normal@pkm-sv10-1"])) == {
        "pk:EN:sv9-69:holo@pkm-sv9-69", "pk:EN:sv10-1:normal@pkm-sv10-1"}


def add_sales(st, printing, prices, days_ago):
    for i, (amount, d) in enumerate(zip(prices, days_ago)):
        when = NOW - timedelta(days=d)
        st.add_observation(MarketObservation(
            id=f"{printing[-8:]}-{i}", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="fixture",
            original_amount=D(str(amount)), currency="TWD", event_time=when, capture_time=when,
            source_url=f"fixture://{i}", printing_candidates=(printing,)), now=NOW)


def test_board_shows_the_year_line_and_the_grading_play(tmp_path):
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    add_sales(st, P, (100, 100, 100), (2, 5, 9))
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW - timedelta(hours=1), "test"), now=NOW)
    trends.refresh(st, NOW, "TWD")
    row = pc.parse_product(payload("https://www.pricecharting.com/game/yugioh-legend-of-blue-eyes/blue-eyes"))
    row["grades"]["PSA 10"]["p"] = 141.25
    store_row(st, P, row, game="ygo")
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert "1 yr · PC" in h and 'class="mpc mpcl down"' in h
    assert "Ungraded · -21%" in h                          # 3.00 -> 2.38 is a fall
    # no live ask: the strip names the most the raw card may cost, after grading, shipping and fees
    assert "Buy raw → grade → sell PSA 10" in h
    assert "no ask under" in h and "max raw" in h and "all-in" in h


def test_grading_strip_buys_at_the_live_ask_and_counts_its_costs(tmp_path):
    from cardman.history import ASK_SCHEMA
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    add_sales(st, P, (100, 100, 100), (2, 5, 9))
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW - timedelta(hours=1), "test"), now=NOW)
    st.db.executescript(ASK_SCHEMA)
    with st.db:
        st.db.execute("INSERT INTO ask_daily VALUES (?,?,?,?,?,?,?)",
                      (P, "|", NOW.date().isoformat(), 300.0, 300.0, 1, "TWD"))
    trends.refresh(st, NOW, "TWD")
    row = pc.parse_product(payload("https://www.pricecharting.com/game/yugioh-legend-of-blue-eyes/blue-eyes"))
    store_row(st, P, row, game="ygo")
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert "buy now" in h and "no ask under" not in h
    # 4520 PSA 10 - 16.75% fees - 300 shipping = 3462.9 net; 1.015 * 300 + 2100 fixed = 2404.5 all-in
    assert "NT$1,058" in h and "all-in" in h


def test_card_page_section_draws_all_six_grades(tmp_path):
    from cardman.ui_pc import section
    st = Store(tmp_path / "e.sqlite")
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW - timedelta(hours=1), "test"), now=NOW)
    store_row(st, P, pc.parse_product(payload()), game="ygo")
    h = section(st, P, "TWD", NOW)
    assert "Graded prices on PriceCharting" in h
    assert '<tr data-g="PSA 10">' in h and "NT$4,520.00" in h and "NT$76.16" in h
    assert "2 sales per week" in h and "PSA 10: 124 graded" in h
    assert 'data-pcsheet' in h and '"PSA 10"' in h
    assert section(st, "p:EN_TCG:NOPE-EN001:x@1", "TWD", NOW) == ""


def test_unswept_rows_say_so_instead_of_guessing(tmp_path):
    from cardman.ui_market import view_market
    st = Store(tmp_path / "e.sqlite")
    add_sales(st, P, (100, 100, 100), (2, 5, 9))
    trends.refresh(st, NOW, "TWD")
    h = view_market(st, NOW, "TWD", "TWD", {}, game="ygo")
    assert "not swept" in h and "Buy raw → grade" not in h
