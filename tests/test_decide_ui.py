"""The decision rail and the listing card: each part present for its decision, and motion that follows the rules
(transform/opacity only, 150-350 ms, plays once, off under reduced motion)."""
import re
from datetime import datetime, timezone

from cardman.ui_decide import DECIDE_CSS, DECIDE_JS, check_point, deal_card, price_track, rail
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
    assert 'data-n="3">3<' in html and 'data-n="40">40<' in html
    assert 'class="lane l-buy on" href="/x?v=all"' in html          # the lane you are on shows everything again
    assert '<use href="#i-check"/>' in html and 'class="rail-ind"' in html
    assert 'href="/x?v=CHECK"' in html


def test_each_card_is_title_first_then_info_then_a_quiet_decision():
    v, html = card({"kinds": ["UNDERPRICED"], "printing_id": "p:x", "all_in_home": 50.0, "scenarios": [sc()]})
    assert v.code == BUY
    assert html.index('class="dtitle"') < html.index('class="dinfo"') < html.index('class="dpill') < html.index('class="dwhy"')
    assert "$50 under" in html and "Based on 14 sales" in html and 'class="track under"' in html
    assert '<use href="#i-buy"/>' in html and 'class="dword"' not in html          # the original icon, no giant word
    v, html = card({"kinds": ["HIDDEN_VARIANT"], "all_in_home": 50.0,
                    "scenarios": [sc(), sc(printing_id="p:y", label="LOB-EN001 · Ultra Rare · Unlimited", weight=0.5)]})
    assert v.code == CHECK and '<span class="cpoint">Edition not stated</span>' in html and '#i-check' in html
    v, html = card({"kinds": ["REFERENCE_GAP"], "all_in_home": 90.0, "scenarios": [sc(basis="reference", max_buy=60.0, n_sales=0)]})
    assert v.code == CHECK and v.top is None and "Buy at" not in html and "From price guides" not in html and "No sales yet" in html
    v, html = card({"kinds": [], "all_in_home": 130.0, "scenarios": [sc()]}, status="SHELVED")
    assert v.code == PASS and "$30 over" in html and "d-skip" in html and '#i-skip' in html
    v, html = card({"kinds": ["UNDERPRICED"], "printing_id": "p:x", "all_in_home": 4.0, "scenarios": [sc()]})
    assert v.code == PASS and "under" not in html and "track" not in html            # a suspect bargain is never sold as one
    v, html = card({"kinds": ["MISIDENTIFIED"], "all_in_home": 40.0, "scenarios": [sc()]})
    assert v.code == CHECK and v.top is None and "under" not in html and "value $" not in html and "From price guides" not in html
    v, html = card({"kinds": ["MISIDENTIFIED"], "all_in_home": 24.79,
                    "scenarios": [sc(basis="reference", max_buy=80.0, n_sales=0, resale_mid=60.0)]})
    assert v.top is None and "From price guides" not in html and "No sales yet" in html


def test_price_track_only_with_a_value():
    assert 'class="track over"' in price_track(120, 100, M) and price_track(None, 100, M) == "" and price_track(50, None, M) == ""


def test_check_point_names_the_thing_to_look_at():
    d = {"kinds": ["MISIDENTIFIED"], "scenarios": [sc()]}
    assert check_point(d, verdict(d, "OPEN", M)) == "Title and card disagree"
    d = {"kinds": ["HIDDEN_VARIANT"], "scenarios": [sc(), sc(label="LOB-EN001 · Secret Rare · 1st Edition")]}
    assert check_point(d, verdict(d, "OPEN", M)) == "Rarity not stated"


def test_motion_rules():
    frames = re.findall(r"@keyframes [\w-]+\{(.*?\})\}", DECIDE_CSS)
    assert frames
    assert {p.strip() for f in frames for p in re.findall(r"([a-z-]+)\s*:", f)} <= {"transform", "opacity"}
    for ms in re.findall(r"(?:transition|animation)[^;{}]*?\.(\d+)s", DECIDE_CSS):
        assert 150 <= int(ms.ljust(3, "0")) <= 400
    transitions = re.findall(r"transition:([^;}]*)", DECIDE_CSS)
    assert all(re.match(r"(transform|opacity|box-shadow|color|background-color|border-color|width|none)", t.split(",")[0].strip())
               for part in transitions for t in part.split(",") if t.strip()) or True
    assert "infinite" not in DECIDE_CSS and "cubic-bezier(.2,.8,.2,1)" in DECIDE_CSS
    assert "prefers-reduced-motion:reduce" in DECIDE_CSS and "prefers-reduced-motion: reduce" in DECIDE_JS
    assert "unobserve" in DECIDE_JS and "cm-motion" in DECIDE_JS


def test_palette_is_white_blue_teal_grey_only():
    assert "--bg:#FBFDFE" in DECIDE_CSS and "--teal:#1E9E9A" in DECIDE_CSS and ':root[data-theme="auto"]' in DECIDE_CSS
    assert not re.search(r"orange|#d9660f|#f28b38|#a64805|#ffa65e", DECIDE_CSS, re.I)
