"""The sold market: what happened after we rated an ask, kept as a permanent record.

eBay's own sold counts (ebay_sold_watch) prove units moved at a known price. Joined with the rating
the engine held at the time, every outcome falls in one of four reads:

  agreed      we said Buy / 2x Check and it sold at the ask, no price cuts
  missed      we said Watch / Skip and it sold at the ask anyway - the market took what we passed on
  dodged      we passed, and it only moved after price cuts, or ended with no sale we could prove
  overcalled  we said Buy / 2x Check, and it only moved after price cuts, or ended with no taker

The record is written to sold_review so it survives the retention sweep, and the gap column keeps
the engine's own reason for the rating - the seed for asking why the market disagreed with us.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

SCHEMA = """
CREATE TABLE IF NOT EXISTS sold_review (
  key TEXT PRIMARY KEY, game TEXT, title TEXT, url TEXT, printing_id TEXT, payload TEXT,
  rating TEXT, ask TEXT, currency TEXT, ask_home REAL,
  sale_price TEXT, sale_cur TEXT, sale_home REAL, units INTEGER, best_offer INTEGER,
  status TEXT, ended TEXT, lived_days REAL, cuts INTEGER, gap TEXT, klass TEXT, why TEXT, recorded_at TEXT);
"""

KLASSES = (("agreed", "Market agreed", "green"),
           ("missed", "Missed prey", "red"),
           ("dodged", "Dodged a bullet", "blue"),
           ("overcalled", "Overcalled", "amber"))
KLASS_LABEL = {k: lab for k, lab, _ in KLASSES}
KLASS_COLOR = {k: c for k, _, c in KLASSES}


def ensure(st):
    st.db.executescript(SCHEMA)


def counts(st, game: str | None = None) -> dict:
    ensure(st)
    if game:
        return {k: n for k, n in st.db.execute("SELECT klass, COUNT(*) FROM sold_review WHERE game=? GROUP BY 1", (game,))}
    return {k: n for k, n in st.db.execute("SELECT klass, COUNT(*) FROM sold_review GROUP BY 1")}


def sync(st, M, now: datetime, *, game: str | None = None) -> int:
    """Refresh the record from the watch table. One row per rated listing with a proven sale or a gone listing."""
    from .ui_listing import verdict, LABEL
    from .fx import FxTable
    tables = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"ebay_sold_watch", "listing", "opportunity"} <= tables:
        return 0
    ensure(st)
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=7))
    except Exception:
        fx = None

    def home(amount, cur):
        if amount is None:
            return None
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return None
        if cur == M.home:
            return amount
        if fx is None:
            return None
        try:
            return float(fx.convert(Decimal(str(amount)), cur or "USD", M.home, now))
        except Exception:
            return None

    has_events = "listing_event" in tables
    rows = st.db.execute(
        "SELECT l.key, l.title, l.url, l.price, l.currency, l.first_seen, l.last_seen, l.ended_at, l.data_json, "
        "o.payload_json, o.status, o.printing_id, w.sold_qty, w.price, w.currency, w.best_offer, w.status "
        "FROM ebay_sold_watch w JOIN listing l ON l.key=w.listing_key "
        "JOIN opportunity o ON o.key=l.key WHERE o.payload_json IS NOT NULL").fetchall()
    n = 0
    for (key, title, url, price, cur, first_seen, last_seen, ended_at, dj, payload, ostatus, pid,
         qty, sprice, scur, bo, wstatus) in rows:
        d = json.loads(payload)
        g = d.get("game") or (json.loads(dj or "{}").get("game") if dj else None) or "ygo"
        if game and g != game:
            continue
        sold = bool(qty and qty > 0 and sprice)
        if not sold and wstatus != "GONE":
            continue
        v = verdict(d, ostatus or "OPEN", M)
        rl = LABEL[v.code]
        cuts = 0
        if has_events:
            cuts = st.db.execute("SELECT COUNT(*) FROM listing_event WHERE key=? AND kind='PRICE_CHANGE' "
                                 "AND CAST(new AS REAL) < CAST(old AS REAL)", (key,)).fetchone()[0]
        end = ended_at or last_seen or now.isoformat()
        try:
            lived = max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(first_seen)).total_seconds() / 86400)
        except (TypeError, ValueError):
            lived = 0.0
        good = v.code in ("BUY", "CHECK")
        sale_txt = f"{sprice} {scur}" if sold else ""
        if sold and good and cuts == 0:
            klass = "agreed"
            why = f"{qty} sold at {sale_txt} with no price cuts. The market paid our number — we said {rl}."
        elif sold and good:
            klass = "overcalled"
            why = f"Our {rl} only moved after {cuts} price cut{'s' if cuts != 1 else ''}, then {qty} sold at {sale_txt}. The market wanted it cheaper."
        elif sold and not good and cuts == 0:
            klass = "missed"
            why = f"{qty} sold at {sale_txt} while we said {rl}. The market took it at full ask — our pass missed this one."
        elif sold and not good:
            klass = "dodged"
            why = f"It only moved after {cuts} price cut{'s' if cuts != 1 else ''}; {qty} sold at {sale_txt}. Passing at the old ask looks right."
        elif good:
            klass = "overcalled"
            why = f"Our {rl} ended after {lived:.0f} days with no sale we could prove. No taker at our number."
        else:
            klass = "dodged"
            why = f"It ended after {lived:.0f} days with no sale we could prove. Our {rl} stands."
        if bo and sold:
            why += " Best Offers were on, so the recorded price is an upper bound."
        ask_home = home(price, cur)
        sale_home = home(sprice, scur) if sold else None
        with st.db:
            st.db.execute("INSERT INTO sold_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT(key) DO UPDATE SET game=excluded.game, title=excluded.title, url=excluded.url, "
                          "printing_id=excluded.printing_id, payload=excluded.payload, rating=excluded.rating, "
                          "ask=excluded.ask, currency=excluded.currency, ask_home=excluded.ask_home, "
                          "sale_price=excluded.sale_price, sale_cur=excluded.sale_cur, sale_home=excluded.sale_home, "
                          "units=excluded.units, best_offer=excluded.best_offer, status=excluded.status, "
                          "lived_days=excluded.lived_days, cuts=excluded.cuts, gap=excluded.gap, klass=excluded.klass, "
                          "ended=excluded.ended, why=excluded.why, recorded_at=excluded.recorded_at",
                          (key, g, title, url, pid, payload, v.code, price, cur, ask_home,
                           sprice if sold else None, scur if sold else None, sale_home,
                           int(qty or 0), int(bo or 0), wstatus, end, lived, cuts, v.reason, klass, why, now.isoformat()))
        n += 1
    return n