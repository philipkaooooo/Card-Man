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


def test_outcome_sold_near_ask_with_hit_rate(tmp_path):
    from cardman import alert_outcomes
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    alerts.scan(st, NOW, "TWD")
    assert alert_outcomes.sweep(st, NOW + timedelta(days=1), "TWD") == {"open": 1, "sold": 0, "gone": 0}
    st.add_observation(MarketObservation(
        id="sale-after", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="ebay_browse:EBAY_US",
        original_amount=D("1500"), currency="TWD", event_time=NOW + timedelta(days=2),
        capture_time=NOW + timedelta(days=2), source_url="fixture://after", printing_candidates=(P,)),
        now=NOW + timedelta(days=2))
    got = alert_outcomes.sweep(st, NOW + timedelta(days=4), "TWD")
    assert got["sold"] == 1 and got["open"] == 0
    state, sale_price, days = st.db.execute("SELECT state, sale_price, days FROM alert_outcome").fetchone()
    assert state == "SOLD_NEAR" and sale_price == 1500 and 1 < days < 3
    s = alert_outcomes.stats(st, now=NOW + timedelta(days=4), home="TWD")
    assert s["sold"] == 1 and s["rate"] == 100 and s["capital"] == 1500 and s["edge"] > 0
    bell = alerts.recent(st, now=NOW + timedelta(days=4))
    assert bell[0]["state"] == "SOLD_NEAR" and bell[0]["hit"] == {"sold": 1, "n": 1}


def test_outcome_gone_without_a_sale(tmp_path):
    from cardman import alert_outcomes
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    alerts.scan(st, NOW, "TWD")
    assert alert_outcomes.sweep(st, NOW + timedelta(days=5), "TWD")["gone"] == 1
    assert st.db.execute("SELECT state FROM alert_outcome").fetchone()[0] == "GONE"
    assert alert_outcomes.stats(st, now=NOW + timedelta(days=5))["rate"] == 0


def test_outcome_confirmed_sale_when_the_listing_ends_sold(tmp_path):
    from cardman import alert_outcomes
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    alerts.scan(st, NOW, "TWD")
    with st.db:
        st.db.execute("UPDATE listing SET kind='sold', ended_at=? WHERE key='l1'",
                      ((NOW + timedelta(days=1)).isoformat(),))
    assert alert_outcomes.sweep(st, NOW + timedelta(days=2), "TWD")["sold"] == 1
    state, sale_price, note = st.db.execute("SELECT state, sale_price, note FROM alert_outcome").fetchone()
    assert state == "SOLD" and sale_price == 1500 and "ended sold" in note


def test_alerts_page_states_the_flow_and_the_rules(tmp_path):
    from cardman import alert_outcomes
    from cardman.ui_alerts import view_alerts
    st = seed(tmp_path)
    add_ask(st, P, 1500)
    alerts.scan(st, NOW, "TWD")
    alert_outcomes.sweep(st, NOW + timedelta(days=5), "TWD")
    h = view_alerts(st, NOW + timedelta(days=5), "TWD")
    assert "did the asks turn into sales" in h.lower()
    assert "inference" in h and "Money flow by week" in h and "Gone" in h
    assert "Blue-Eyes" in h or "LOB-EN001" in h


def _outcome(st, sid, pid, at, ask, typ, state):
    from cardman.alert_outcomes import _SCHEMA
    st.db.executescript(_SCHEMA)
    with st.db:
        st.db.execute("INSERT OR REPLACE INTO alert_outcome (signal_id, printing_id, key, at, ask, typical, state, "
                      "checked) VALUES (?,?,?,?,?,?,?,?)", (sid, pid, None, at.isoformat(), ask, typ, state, at.isoformat()))


def test_dead_print_stops_alerting(tmp_path):
    from cardman import alert_outcomes
    st = seed(tmp_path)
    for i in range(4):                                   # four past alerts on P, none of them sold
        _outcome(st, f"old{i}", P, NOW - timedelta(days=30 - i), 1500, 2000, "GONE")
    assert alert_outcomes.sell_through(st)[0][P]["n"] == 4
    add_ask(st, P, 1500)
    assert alerts.scan(st, NOW, "TWD") == 0


def test_one_good_print_keeps_alerting(tmp_path):
    from cardman import alert_outcomes
    st = seed(tmp_path)
    for i in range(4):
        _outcome(st, f"old{i}", P, NOW - timedelta(days=30 - i), 1500, 2000, "SOLD_NEAR")
    rates, _ = alert_outcomes.sell_through(st)
    assert rates[P]["rate"] > alert_outcomes.DEAD_RATE
    add_ask(st, P, 1500)
    assert alerts.scan(st, NOW, "TWD") == 1


def test_bell_ranks_by_money_times_sell_through(tmp_path):
    st = seed(tmp_path)
    for i, d in enumerate((2, 5, 9)):
        when = NOW - timedelta(days=d)
        st.add_observation(MarketObservation(
            id=f"q{i}", evidence_type=ET.MARKETPLACE_MARKED_SOLD, source="ebay_browse:EBAY_US",
            original_amount=D("2000"), currency="TWD", event_time=when, capture_time=when,
            source_url=f"fixture://q{i}", printing_candidates=(Q,)), now=NOW)
    trends.refresh(st, NOW, "TWD")
    for i in range(4):                                  # P's past alerts sold; Q's mostly did not
        _outcome(st, f"p-old{i}", P, NOW - timedelta(days=40, minutes=i), 1500, 2000, "SOLD_NEAR")
        _outcome(st, f"q-old{i}", Q, NOW - timedelta(days=40, minutes=i), 1500, 2000,
                 "SOLD_NEAR" if i == 0 else "GONE")
    add_ask(st, P, 1500)                                # same money on the table as Q's ask
    add_ask(st, Q, 1500, key="l2")                      # the one difference is which print sells
    assert alerts.scan(st, NOW, "TWD") == 2
    got = alerts.recent(st, now=NOW)
    assert [g["pid"] for g in got] == [P, Q]
    assert got[0]["rate"] > got[1]["rate"] and got[0]["weight"] > got[1]["weight"]
