"""Under-typical asks: one signal per printing per price point, written when the batch lands, and a bell that reads them."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import alerts, pc, trends
from cardman.card_market import _SCHEMA as CARD_SCHEMA
from cardman.engine import _SCHEMA as ENGINE_SCHEMA
from cardman.fx import FxRate
from cardman.models import EvidenceType as ET, MarketObservation
from cardman.store import Store

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
P = "p:EN_TCG:LOB-EN001:legend-of-blue-eyes:ultra-rare@89631139"
Q = "p:EN_TCG:MAMS-EN003:the-guiding-flute:mams-en003@12345678"


def seed(tmp_path):
    st = Store(tmp_path / "e.sqlite")
    for i, d in enumerate((2, 5, 9)):
        when = NOW - timedelta(days=d)
        st.add_observation(MarketObservation(
            id=f"s{i}", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="ebay_browse:EBAY_US",
            original_amount=D("2000"), currency="TWD", event_time=when, capture_time=when,
            source_url=f"fixture://s{i}", printing_candidates=(P,)), now=NOW)
    st.add_fx(FxRate("USD", "TWD", D("32"), NOW - timedelta(hours=1), "test"), now=NOW)
    st.db.executescript(ENGINE_SCHEMA + CARD_SCHEMA)
    trends.refresh(st, NOW, "TWD")
    return st


def add_ask(st, pid, price, key="l1"):
    with st.db:
        st.db.execute("INSERT INTO listing (key, kind, price, currency, url, title, last_seen, first_seen) "
                      "VALUES (?, 'ask', ?, 'TWD', 'https://www.ebay.com/itm/1', 'Blue-Eyes LOB-EN001', ?, ?)",
                      (key, str(price), NOW.isoformat(), NOW.isoformat()))
        st.db.execute("INSERT INTO card_listing (passcode, key, card_prob, printing_id, printing_prob, lot, status) "
                      "VALUES ('89631139', ?, 0.97, ?, 0.94, 0, 'SEEN')", (key, pid))


def test_under_typical_ask_alerts_once_per_price_point(tmp_path):
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    assert alerts.scan(st, NOW, "TWD") == 1
    n = st.db.execute("SELECT COUNT(*) FROM market_signal WHERE kind='ASK_UNDER'").fetchone()[0]
    assert n == 1 and alerts.scan(st, NOW, "TWD") == 0        # the same ask seen again is not news
    row = json.loads(st.db.execute("SELECT detail_json FROM market_signal WHERE kind='ASK_UNDER'").fetchone()[0])
    assert row["ask"] == 1500.0 and row["typical"] == 2000.0 and row["pct"] == 25.0
    assert "25% under" in row["text"]


def test_an_ask_at_or_above_typical_is_not_an_alert(tmp_path):
    st = seed(tmp_path)
    add_ask(st, P, 2000)
    assert alerts.scan(st, NOW, "TWD") == 0
    add_ask(st, Q, 1500, key="l2")                            # no typical for Q: silence, not a guess
    assert alerts.scan(st, NOW, "TWD") == 0


def test_recent_carries_the_card_page_for_the_bell(tmp_path):
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    alerts.scan(st, NOW, "TWD")
    got = alerts.recent(st, now=NOW)
    assert len(got) == 1 and got[0]["href"] == "/card/89631139"
    assert got[0]["pct"] == 25.0 and got[0]["cur"] == "TWD" and got[0]["label"]


def test_every_page_carries_the_bell(tmp_path):
    from cardman.ui import page
    h = page("t", "<h1>t</h1>", nav="home", store=str(tmp_path / "x.sqlite")).decode()
    assert 'id="abell"' in h and 'id="apop"' in h and "Under-typical asks as they land" in h
