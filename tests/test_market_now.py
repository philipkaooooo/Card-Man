"""The Now zone: under-typical asks and raw→PSA 10 picks, with the seller's own photo where a listing carries one."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import trends
from cardman.engine import _SCHEMA as ENGINE_SCHEMA
from cardman.history import ASK_SCHEMA
from cardman.listing_detail import _SCHEMA as DETAIL_SCHEMA
from cardman.models import EvidenceType as ET, MarketObservation
from cardman.store import Store

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
P = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes:ultra-rare@89631139"
PK = "pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199"


def sale(st, i, amount, days, printing=P):
    when = NOW - timedelta(days=days)
    st.add_observation(MarketObservation(
        id=f"t{i}", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="fixture", original_amount=D(str(amount)),
        currency="TWD", event_time=when, capture_time=when, source_url=f"fixture://{i}",
        printing_candidates=(printing,), observed_condition=None), now=NOW)


def ask_under(st, printing=P, ask=60.0):
    st.db.executescript(ASK_SCHEMA)
    with st.db:
        st.db.execute("INSERT INTO ask_daily VALUES (?,?,?,?,?,?,?)",
                      (printing, "|", NOW.date().isoformat(), ask, ask, 3, "TWD"))


def live_listing(st, item_id="123456789"):
    from cardman.card_market import _SCHEMA as MARKET_SCHEMA
    st.db.executescript(ENGINE_SCHEMA)
    st.db.executescript(MARKET_SCHEMA)
    key = "ebay:1"
    with st.db:
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (key, "ebay", item_id, "EBAY_US", "Blue-Eyes raw", f"https://www.ebay.com/itm/{item_id}", None,
                       "ask", "60", "TWD", None, NOW.isoformat(), NOW.isoformat(), None, None, "{}"))
        st.db.execute("INSERT INTO card_listing VALUES (?,?,?,?,?,?,?,?,?)",
                      ("89631139", key, 0.99, P, 0.99, "LOB-EN001 · ultra rare", 60.0, "OK", 0))


def zone(st):
    from cardman.ui_now import view_now
    return view_now(st, NOW, "TWD", "TWD", {}, game="ygo")


def test_under_typical_tiles_carry_the_sellers_own_photo(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i in range(4):
        sale(st, i, 100, i + 1)
    ask_under(st)
    live_listing(st)
    trends.refresh(st, NOW, "TWD")
    h = zone(st)
    assert 'class="nz-tile"' in h and "under typical now" in h.lower()
    assert 'class="nz-tag under">40% under' in h                     # (100 - 60) / 100
    assert "/img/listing/123456789/0.jpg?s=m" in h                   # the listing's own picture, not artwork
    assert "onerror" in h and "/img/card/89631139.jpg?size=small" in h   # and the official art as stand-in


def test_no_listing_row_falls_back_to_official_art(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i in range(4):
        sale(st, i, 100, i + 1)
    ask_under(st)
    trends.refresh(st, NOW, "TWD")
    h = zone(st)
    assert 'src="/img/card/89631139.jpg?size=small"' in h and "/img/listing/" not in h
    assert "Listing link expired" in h                               # the snapshot has no live URL to open


def test_a_clean_store_says_so_instead_of_inventing_a_deal(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    h = zone(st)
    assert "Nothing to act on" in h and 'class="nz-tile"' not in h


def test_pokemon_uses_its_own_board_and_art_route(tmp_path):
    from cardman.ui_now import view_now
    st = Store(tmp_path / "e.sqlite")
    for i in range(4):
        sale(st, i, 100, i + 1, printing=PK)
    ask_under(st, printing=PK)
    trends.refresh(st, NOW, "TWD")
    h = view_now(st, NOW, "TWD", "TWD", {}, game="pkm")
    assert "Now · Pokémon" in h and "40% under" in h
    assert "/pkm/img/pkm/sv3pt5-199.png?size=small" in h
    assert 'href="/pkm/market"' in h


def test_market_menu_opens_to_the_zone():
    from cardman.ui import NAV_OF, SUBS
    assert ("now", "Under typical & grading", "/market/now") in SUBS["market"]
    assert NAV_OF["now"] == "market"


def test_scrydex_art_is_served_not_placeheld(tmp_path):
    from cardman.cardinfo import ImageCache
    body = b"\x89PNG\r\n\x1a\n" + b"0" * 200
    seen = []

    def get(url):
        seen.append(url)
        return body, "image/png"
    ic = ImageCache(tmp_path, get=get)
    got, ctype = ic.pkm("me2pt5-283", "https://images.scrydex.com/pokemon/me2pt5-283/small")
    assert got == body and ctype == "image/png" and seen
