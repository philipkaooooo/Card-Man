"""Did the alert turn into money? Every under-typical signal is followed to its end: the listing ended sold, it
vanished and a sale of the same print landed at the ask within weeks, or it vanished with nothing behind it.

This is the loop that keeps the alerts honest. The chase is an inference, not a receipt: a vanished listing has no
bought-by record here, so a sale counts as the alert being real only when it is the same printing, raw, single copy,
inside the ask band and inside the window. Confirmed endings (kind went sold / accepted offer) are direct evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

GONE_AFTER = timedelta(days=3)     # an ask unseen this long is gone (the board's own staleness window)
SALE_WINDOW = timedelta(days=21)   # a sale this soon after the alert can be the copy that was alerted
NEAR_LOW, NEAR_HIGH = 0.75, 1.25   # the sale has to land in the ask's price band, against ask and typical

_SCHEMA = """CREATE TABLE IF NOT EXISTS alert_outcome (
  signal_id TEXT PRIMARY KEY, printing_id TEXT, key TEXT, at TEXT NOT NULL, ask REAL, typical REAL,
  state TEXT NOT NULL, checked TEXT NOT NULL, gone_at TEXT, sale_id TEXT, sale_at TEXT, sale_price REAL,
  days REAL, note TEXT);
CREATE INDEX IF NOT EXISTS outcome_at ON alert_outcome(at);
CREATE INDEX IF NOT EXISTS outcome_pid ON alert_outcome(printing_id, state);"""

SOLD_STATES = ("SOLD", "SOLD_NEAR")
ENDED_SALE = ("sold", "accepted_offer", "transaction")


def _tables(st) -> set[str]:
    return {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _sales(st, home: str, pids: list[str]) -> dict[str, list[tuple[datetime, float, str]]]:
    """Raw single-copy sales per printing, priced in the home currency at their own sale date."""
    from .history import Converter, card_sales
    conv = Converter(st, home, home)
    out: dict[str, list] = {}
    for (pid, gc, gr), obs in card_sales(st, pids).items():
        if gc:                      # an alert is a raw ask; a slab sale does not prove it
            continue
        for o in obs:
            v = conv(o.original_amount, o.currency, o.event_time, fetch=False)
            if v and v > 0:
                out.setdefault(pid, []).append((o.event_time, float(v), o.id))
    return out


def _upsert(st, row: tuple) -> None:
    st.db.execute(
        "INSERT INTO alert_outcome (signal_id, printing_id, key, at, ask, typical, state, checked, gone_at, sale_id, "
        "sale_at, sale_price, days, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(signal_id) DO UPDATE SET state=excluded.state, checked=excluded.checked, gone_at=excluded.gone_at, "
        "sale_id=excluded.sale_id, sale_at=excluded.sale_at, sale_price=excluded.sale_price, days=excluded.days, "
        "note=excluded.note", row)


def sweep(st, now: datetime, home: str = "TWD") -> dict:
    """Follow every open signal one step. Returns {'open': n, 'sold': n_new, 'gone': n_new}."""
    if "market_signal" not in _tables(st):
        return {"open": 0, "sold": 0, "gone": 0}
    st.db.executescript(_SCHEMA)
    rows = st.db.execute(
        "SELECT s.id, s.printing_id, s.at, s.detail_json, o.state, o.state IS NULL "
        "FROM market_signal s LEFT JOIN alert_outcome o ON o.signal_id=s.id "
        "WHERE s.kind='ASK_UNDER' AND (o.signal_id IS NULL OR o.state='OPEN') ORDER BY s.at").fetchall()
    if not rows:
        return {"open": 0, "sold": 0, "gone": 0}
    pids = sorted({r[1] for r in rows if r[1]})
    sales = _sales(st, home, pids) if pids else {}
    fresh = (now - GONE_AFTER).isoformat()
    opened = sold = gone = 0
    with st.db:
        for sid, pid, at_s, dj, prior, is_new in rows:
            try:
                d = json.loads(dj or "{}")
            except Exception:
                d = {}
            ask, typ, key = d.get("ask"), d.get("typical"), d.get("key")
            if ask is None or typ is None:
                continue
            at = datetime.fromisoformat(at_s)
            state, gone_at, sale_id, sale_at, sale_price, days, note = "OPEN", None, None, None, None, None, ""
            live = None
            if key:
                lr = st.db.execute("SELECT kind, price, currency, ended_at, last_seen FROM listing WHERE key=?",
                                   (key,)).fetchone()
                if lr:
                    kind, price, cur, ended, last_seen = lr
                    if ended is None and (last_seen or "") >= fresh:
                        live = True
                    elif kind in ENDED_SALE:
                        live = False
                        state = "SOLD"
                        gone_at = ended or now.isoformat()
                        sale_at = gone_at
                        try:
                            from decimal import Decimal
                            from .fx import FxTable
                            table = FxTable(st.fx_rates(), max_age=timedelta(days=5))
                            sale_price = (float(price) if cur == home else
                                          float(table.convert(Decimal(str(price)), cur, home, datetime.fromisoformat(gone_at))))
                        except Exception:
                            sale_price = None
                        note = f"the listing itself ended {kind.replace('_', ' ')}"
                    elif kind == "unsold":
                        live = False
                        state = "GONE"
                        gone_at = ended or now.isoformat()
                        note = "the listing ended unsold"
                    else:
                        live = False
                        gone_at = ended or last_seen or now.isoformat()
                else:
                    live = False
                    note = "the listing is no longer on the board"
            if state == "OPEN" and live is False:
                # vanished: the board keeps a listing 3 days past its last sighting, so gone time is sighting + 3d
                if gone_at is None:
                    lr2 = st.db.execute("SELECT last_seen FROM listing WHERE key=?", (key,)).fetchone() if key else None
                    gone_at = ((datetime.fromisoformat(lr2[0]) + GONE_AFTER) if lr2 and lr2[0] else now).isoformat()
                lo, hi = ask * NEAR_LOW, typ * NEAR_HIGH
                best = None
                for t, v, oid in sales.get(pid, ()):
                    if t <= at or t > at + SALE_WINDOW or not (lo <= v <= hi):
                        continue
                    if best is None or abs(v - ask) < abs(best[1] - ask):
                        best = (t, v, oid)
                if best:
                    state = "SOLD_NEAR"
                    sale_at, sale_price, sale_id = best[0].isoformat(), round(best[1], 2), best[2]
                    days = round((best[0] - at).total_seconds() / 86400, 1)
                    note = (f"{pid_label(d)} sold {days:g} d after the alert at {sale_price:,.0f} {home} - "
                            f"inside the ask band, likely the copy or its twin")
                else:
                    state = "GONE"
                    days = round((datetime.fromisoformat(gone_at) - at).total_seconds() / 86400, 1) if gone_at else None
                    note = "gone from the board and no same-print sale landed near the ask within 21 days"
            if state == "OPEN" and live is None and is_new:
                # a pre-key signal: no listing to follow. It stays open until the ask id recurs with a key.
                continue
            if state == "OPEN":
                opened += 1
            elif state in SOLD_STATES:
                sold += 1
            else:
                gone += 1
            _upsert(st, (sid, pid, key, at_s, float(ask), float(typ), state, now.isoformat(), gone_at, sale_id,
                         sale_at, sale_price, days, note))
    return {"open": opened, "sold": sold, "gone": gone}


def pid_label(d: dict) -> str:
    return d.get("label") or "the print"


def stats(st, days: int = 60, now: datetime | None = None, home: str = "TWD") -> dict:
    """The scoreboard: how many alerts, how many ended sold vs gone, money caught, edge left on the table."""
    if "alert_outcome" not in _tables(st):
        return {"n": 0, "live": 0, "sold": 0, "gone": 0, "rate": None, "capital": 0.0, "edge": 0.0, "weeks": []}
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    k_buy, fee_pct = 0.0, 0.0
    try:
        from .engine import load_profile
        from .alerts import _fees
        k_buy, fee_pct = _fees(load_profile())
    except Exception:
        pass
    rows = st.db.execute("SELECT state, ask, typical, at FROM alert_outcome WHERE at>=? ORDER BY at", (cutoff,)).fetchall()
    n = len(rows)
    live = sum(1 for s, *_ in rows if s == "OPEN")
    sold = sum(1 for s, *_ in rows if s in SOLD_STATES)
    gone = sum(1 for s, *_ in rows if s == "GONE")
    resolved = sold + gone
    capital = sum(a or 0 for s, a, *_ in rows if s in SOLD_STATES)
    edge = sum(max(0.0, (t or 0) * (1 - fee_pct) - k_buy * (a or 0)) for s, a, t, _ in rows if s in SOLD_STATES)
    weeks: dict[str, dict] = {}
    for s, a, t, at in rows:
        try:
            iso = datetime.fromisoformat(at).isocalendar()
            w = f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            continue
        slot = weeks.setdefault(w, {"week": w, "n": 0, "sold": 0, "capital": 0.0, "edge": 0.0})
        slot["n"] += 1
        if s in SOLD_STATES:
            slot["sold"] += 1
            slot["capital"] += a or 0
            slot["edge"] += max(0.0, (t or 0) * (1 - fee_pct) - k_buy * (a or 0))
    return {"n": n, "live": live, "sold": sold, "gone": gone, "rate": (sold / resolved * 100) if resolved else None,
            "capital": round(capital, 2), "edge": round(edge, 2), "weeks": [weeks[k] for k in sorted(weeks)],
            "cur": home}


def ledger(st, days: int = 60, limit: int = 60, now: datetime | None = None) -> list[dict]:
    """Every followed signal, newest first, with the card label and page it belongs to."""
    if "alert_outcome" not in _tables(st):
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    rows = st.db.execute(
        "SELECT o.signal_id, o.printing_id, o.at, o.ask, o.typical, o.state, o.days, o.sale_price, o.sale_at, o.note, "
        "s.detail_json FROM alert_outcome o LEFT JOIN market_signal s ON s.id=o.signal_id "
        "WHERE o.at>=? ORDER BY o.at DESC LIMIT ?", (cutoff, limit)).fetchall()
    from .games import card_href
    from .ui_market import _card_key
    out = []
    for sid, pid, at, ask, typ, state, days, sale_price, sale_at, note, dj in rows:
        try:
            d = json.loads(dj or "{}")
        except Exception:
            d = {}
        key = _card_key(pid) if pid else None
        out.append({"id": sid, "pid": pid, "at": at, "ask": ask, "typical": typ, "state": state, "days": days,
                    "sale_price": sale_price, "sale_at": sale_at, "note": note,
                    "label": d.get("label") or pid, "href": card_href(key) if key else None})
    return out
