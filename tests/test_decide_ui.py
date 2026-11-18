"""The decision rail and the listing card: each part present for its decision, and motion that follows the rules
(transform/opacity only, 150-350 ms, plays once, off under reduced motion)."""
import re
from datetime import datetime, timezone

from cardman.ui_decide import DECIDE_CSS, DECIDE_JS, check_point, deal_card, rail, target_ring, value_bar
from cardman.ui_listing import BUY, CHECK, PASS, WATCH, verdict

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)
M = lambda v, sign=False: "-" if v is None else f"${float(v):,.0f}"


def sc(**kw):
    base = {"printing_id": "p:EN_TCG:LOB-EN001:lob:ur@89631139|ed:1st", "label": "LOB-EN001 · Ultra Rare · 1st Edition", "weight": 1.0,
            "basis": "sales", "resale_mid": 100.0, "max_buy": 70.0, "profit_mid": 20.0, "n_sales": 14}
    base.update(kw)
    return base


def card(d, status="OPEN"):
    d = {"title": "Blue-Eyes White Dragon LOB-EN001", "game": "ygo", **d}
    v = verdict(d, status, M)
    return v, deal_card("ebay_browse:EBAY_US:1", d, v, None, M, NOW)


def test_rail_has_four_lanes_with_counts_and_toggles_back():
    html = rail({BUY: 3, CHECK: 2, WATCH: 9, PASS: 40}, BUY, lambda code, on: "/x?v=all" if on else f"/x?v={code}")
    assert [w for w in ("Buy", "2x Check", "Watch", "Skip") if f'lane-w">{w}<' in html] == ["Buy", "2x Check", "Watch", "Skip"]
    assert 'lane-n">3<' in html and 'lane-n">40<' in html
    assert 'class="lane l-buy on" href="/x?v=all"' in html          # the lane you are on shows everything again
    assert 'href="/x?v=CHECK"' in html


def test_each_decision_card_shows_its_own_part():
    v, html = card({"kinds": ["UNDERPRICED"], "printing_id": "p:x", "all_in_home": 50.0, "scenarios": [sc()]})
    assert v.code == BUY and "$50 under value" in html and 'class="vbar under"' in html and "Based on 14 recent sales" in html
    v, html = card({"kinds": ["HIDDEN_VARIANT"], "all_in_home": 50.0,
                    "scenarios": [sc(), sc(printing_id="p:y", label="LOB-EN001 · Ultra Rare · Unlimited", weight=0.5)]})
    assert v.code == CHECK and '<span class="cpoint">Edition not stated</span>' in html and 'class="sweep"' in html
    v, html = card({"kinds": ["REFERENCE_GAP"], "all_in_home": 90.0, "scenarios": [sc(basis="reference", max_buy=60.0, n_sales=0)]})
    assert v.code == WATCH and "Buy at $60 or less" in html and 'class="ring"' in html
    v, html = card({"kinds": [], "all_in_home": 130.0, "scenarios": [sc()]}, status="SHELVED")
    assert v.code == PASS and "$30 over value" in html and 'class="dwhy"' in html and "d-skip" in html
    v, html = card({"kinds": ["UNDERPRICED"], "printing_id": "p:x", "all_in_home": 4.0, "scenarios": [sc()]})
    assert v.code == PASS and "under value" not in html and "vbar" not in html and "$4 total" in html   # a suspect bargain is never sold as one
    v, html = card({"kinds": ["MISIDENTIFIED"], "all_in_home": 40.0, "scenarios": [sc()]})
    assert v.code == CHECK and "under value" not in html and "$40 total" in html


def test_value_bar_and_ring_say_the_right_thing():
    assert 'class="vbar over"' in value_bar(120, 100, 70, M, PASS)
    assert value_bar(None, 100, None, M, BUY) == ""                 # no number is ever invented
    assert ">67%<" in target_ring(90, 60, M) and target_ring(90, None, M) == ""


def test_check_point_names_the_thing_to_look_at():
    d = {"kinds": ["MISIDENTIFIED"], "scenarios": [sc()]}
    assert check_point(d, verdict(d, "OPEN", M)) == "Title and card disagree"
    d = {"kinds": ["HIDDEN_VARIANT"], "scenarios": [sc(), sc(label="LOB-EN001 · Secret Rare · 1st Edition")]}
    assert check_point(d, verdict(d, "OPEN", M)) == "Rarity not stated"


def test_motion_rules():
    frames = re.findall(r"@keyframes [\w-]+\{(.*?\})\}", DECIDE_CSS)
    assert frames
    props = {p.strip() for f in frames for p in re.findall(r"([a-z-]+)\s*:", f)}
    assert props <= {"transform", "opacity"}                        # transform/opacity only
    for dur in re.findall(r"animation:[\w-]+ \.(\d+)s", DECIDE_CSS):
        assert 150 <= int(dur.ljust(3, "0")) <= 350
    assert "infinite" not in DECIDE_CSS                             # never loops
    assert "prefers-reduced-motion:reduce" in DECIDE_CSS and "animation:none!important" in DECIDE_CSS
    assert "unobserve" in DECIDE_JS                                 # each card plays once, when it first comes into view


def test_palette_has_light_and_dark_tokens():
    assert "--orange:" in DECIDE_CSS and "--bg:#e9f1f6" in DECIDE_CSS
    assert ':root[data-theme="auto"]' in DECIDE_CSS                 # dark follows the system
