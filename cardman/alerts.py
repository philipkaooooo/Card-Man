"""Under-typical asks as they land: a signal the moment a batch is analyzed, not at the end of the pass.

The math is the market page's Under typical section: raw in, raw out, the ask has to beat the typical sold price
after the fees to sell it, and has to sit within 3x of it so a different printing never reads as a steal. One
signal per printing per price point, so a listing that stays cheap does not re-alert every pass.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

TYPICAL_OK = 3.0          # same-printing gate: ask within 3x either way of the typical
MIN_DISCOUNT = 0.02       # under by less than this is noise
MIN_SALES = 3             # thin rows do not get alerts

_SCHEMA = """CREATE TABLE IF NOT EXISTS market_signal (id TEXT PRIMARY KEY, kind TEXT, printing_id TEXT, at TEXT, detail_json TEXT);"""


def _fees(profile: dict) -> tuple[float, float]:
    route = dict((profile.get("routes") or {}).get("INTL") or {})
    exit_c = dict(profile.get("exit") or {})
    k_buy = 1 + float(route.get("payment_fx_spread_pct") or 0)
    fee_pct = (float(exit_c.get("platform_fee_pct") or 0) + float(exit_c.get("returns_reserve_pct") or 0)
               + float(exit_c.get("payout_fx_spread_pct") or 0))
    return k_buy, fee_pct


def _tables(st) -> set[str]:
    return {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _game(pid: str) -> str:
    return "pkm" if pid.startswith("pk:") else "ygo"


def _asks(st, now: datetime, home: str, pids: list[str] | None) -> dict[str, dict]:
    """Cheapest live ask per printing, converted to home currency, with the listing it came from so an outcome
    can follow it. Same gates the snapshot uses: 95% on the card, 90% on the printing, no lots, nothing ended,
    nothing unseen for three days."""
    have = _tables(st)
    if not {"card_listing", "listing"} <= have:
        return {}
    from .fx import FxTable
    from .history import ASK_CARD_PROB, ASK_PRINTING_PROB
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=3))
    except Exception:
        fx = None
    fresh = (now - timedelta(days=3)).isoformat()
    where = ""
    args: tuple = (ASK_CARD_PROB, ASK_PRINTING_PROB, fresh)
    if pids:
        where = f" AND c.printing_id IN ({','.join('?' * len(pids))})"
        args += tuple(pids)
    rows = st.db.execute(
        "SELECT c.printing_id, l.price, l.currency, l.key, l.url FROM card_listing c JOIN listing l ON l.key=c.key "
        "WHERE c.card_prob>=? AND c.printing_prob>=? AND c.lot=0 AND l.kind='ask' AND c.status<>'REJECTED' "
        "AND l.ended_at IS NULL AND l.last_seen>=?" + where, args)
    best: dict[str, dict] = {}
    for pid, price, cur, lkey, url in rows:
        try:
            v = float(price) if cur == home else (float(fx.convert(Decimal(str(price)), cur, home, now)) if fx else None)
        except Exception:
            continue
        if v is None or v <= 0:
            continue
        if pid not in best or v < best[pid]["ask"]:
            best[pid] = {"ask": v, "key": lkey, "url": url}
    return best


def _typicals(st, pids: list[str] | None) -> dict[str, dict]:
    have = _tables(st)
    if "price_trend" not in have:
        return {}
    where = ""
    args: tuple = ()
    if pids:
        where = f" AND printing_id IN ({','.join('?' * len(pids))})"
        args = tuple(pids)
    rows = st.db.execute(
        "SELECT printing_id, median, median_30, n_30, n_sales FROM price_trend WHERE gkey='|' AND median IS NOT NULL"
        + where, args)
    out = {}
    for pid, med, m30, n30, n in rows:
        out[pid] = {"fair": m30 if (n30 or 0) >= MIN_SALES else med, "n": n or 0}
    return out


def scan(st, now: datetime, home: str, pids: list[str] | None = None) -> int:
    """Write one ASK_UNDER signal per printing per price point. Returns how many are new. `pids` limits the scan
    to a batch that just came in; None sweeps everything the board can price."""
    from .engine import load_profile
    from .trends import label_for
    try:
        profile = load_profile()
    except Exception:
        profile = {}
    k_buy, fee_pct = _fees(profile)
    asks = _asks(st, now, home, pids)
    if not asks:
        return 0
    try:
        from .alert_outcomes import dead, sell_through
        rates, _prior = sell_through(st)
    except Exception:
        rates, dead = {}, lambda rate, n: False
    add = list(asks)
    typ = _typicals(st, add)
    st.db.executescript(_SCHEMA)
    added = 0
    day = now.date().isoformat()
    with st.db:
        for pid, info in asks.items():
            ask = info["ask"]
            t = typ.get(pid)
            if not t or t["n"] < MIN_SALES:
                continue
            fair = float(t["fair"])
            if fair <= 0 or not (fair / TYPICAL_OK <= ask <= fair * TYPICAL_OK):
                continue
            disc = (fair - ask) / fair
            if disc < MIN_DISCOUNT or fair * (1 - fee_pct) - k_buy * ask <= 0:
                continue
            hist = rates.get(pid)
            if hist and dead(hist["rate"], hist["n"]):
                continue        # a print whose past alerts keep dying does not get a new one
            label = label_for(pid)
            detail = {"label": label, "ask": round(ask, 2), "typical": round(fair, 2),
                      "pct": round(disc * 100, 1), "cur": home, "game": _game(pid), "day": day,
                      "key": info["key"], "url": info["url"],
                      "text": f"{label} asked at {ask:,.0f} {home}, {disc * 100:.0f}% under its typical "
                              f"{fair:,.0f} {home} after fees."}
            sid = f"ASK_UNDER:{pid}:{ask:.2f}"
            dj = json.dumps(detail, ensure_ascii=False)
            got = st.db.execute("INSERT OR IGNORE INTO market_signal VALUES (?,?,?,?,?)",
                                (sid, "ASK_UNDER", pid, now.isoformat(), dj)).rowcount
            if not got:
                # a signal written before the listing key was kept: attach it now so the outcome can follow it
                got = st.db.execute("UPDATE market_signal SET detail_json=? WHERE id=? AND instr(detail_json, '\"key\"')=0",
                                    (dj, sid)).rowcount
            added += got
    return added


def recent(st, days: int = 7, limit: int = 30, now: datetime | None = None) -> list[dict]:
    """The newest under-typical asks, for the bell. Each carries its card page so a click lands somewhere."""
    if "market_signal" not in _tables(st):
        return []
    from datetime import timezone
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    rows = st.db.execute("SELECT id, at, printing_id, detail_json FROM market_signal "
                         "WHERE kind IN ('ASK_UNDER','CHASE_UNDER') AND at>=? ORDER BY at DESC LIMIT ?",
                         (cutoff, limit)).fetchall()
    from .games import card_href
    from .ui_market import _card_key
    states, hits = {}, {}
    have = _tables(st)
    if "alert_outcome" in have:
        for oid, pid, state, days, sale in st.db.execute(
                "SELECT signal_id, printing_id, state, days, sale_price FROM alert_outcome"):
            states[oid] = (state, days, sale)
        for pid, sold, n in st.db.execute(
                "SELECT printing_id, SUM(state IN ('SOLD','SOLD_NEAR')), COUNT(*) FROM alert_outcome "
                "WHERE state<>'OPEN' GROUP BY printing_id"):
            if n:
                hits[pid] = {"sold": int(sold or 0), "n": int(n)}
    out = []
    for sid, at, pid, dj in rows:
        try:
            d = json.loads(dj or "{}")
        except Exception:
            d = {}
        chase = sid.startswith("CHASE_UNDER:")
        key = d.get("href") or (None if chase else _card_key(pid))
        href = (key or "/pile") if chase else (card_href(key) if key else None)
        out.append({"id": sid, "at": at, "pid": pid, "label": d.get("label") or pid,
                    "ask": d.get("ask"), "typical": d.get("typical"), "pct": d.get("pct"),
                    "cur": d.get("cur") or "TWD", "game": d.get("game") or _game(pid),
                    "href": href,
                    "chase": chase,
                    "text": d.get("text") or "",
                    "state": (states.get(sid) or ("OPEN", None, None))[0],
                    "days": (states.get(sid) or ("OPEN", None, None))[1],
                    "sale": (states.get(sid) or ("OPEN", None, None))[2],
                    "hit": hits.get(pid), "rate": None, "hist_n": None, "dead": False, "weight": 0.0})
    from .alert_outcomes import dead, sell_through
    rates, prior = sell_through(st)
    try:
        from .engine import load_profile
        k_buy, fee_pct = _fees(load_profile())
    except Exception:
        k_buy, fee_pct = 1.0, 0.0
    for it in out:
        hist = rates.get(it["pid"])
        rate = hist["rate"] if hist else prior
        n_hist = hist["n"] if hist else 0
        edge = (it.get("typical") or 0) * (1 - fee_pct) - k_buy * (it.get("ask") or 0)
        it.update(rate=round(rate, 3), hist_n=n_hist, dead=dead(rate, n_hist),
                  weight=round(rate * max(edge, 0), 2))
    out.sort(key=lambda x: (x["weight"], x["at"]), reverse=True)   # money at stake, then recency
    return out
