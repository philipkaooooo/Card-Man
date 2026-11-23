"""The market board: every printing CardMan can price from real sales, with its trend and the gap to live asks.

One row per (printing, grade). Raw and each slab grade are separate series, never pooled. The numbers come
from the same rules as a card page: lineage dedupe, one-to-one cross-source dedupe, excluded flags removed,
each sale converted to the home currency at its own sale date. Trend is Theil-Sen on the individual sales
with a seeded bootstrap interval; 90-day change compares the last 30 days with days 90-120 ago. No
interpolation: a period without a sale has no value.

The board is materialized into `price_trend` so a page load never recomputes 2,000 series. `refresh` runs at
the end of every engine tick and from the CLI.

    python -m cardman.trends refresh [--store ...] [--home TWD]
    python -m cardman.trends top [--label rising] [--min-sales 3] [--sort move] [--limit 30]
"""
from __future__ import annotations

import argparse
import statistics
from datetime import date, datetime, timedelta, timezone

from .history import card_sales, plain_trend, summarize, Converter

UTC = timezone.utc
SCHEMA = """
CREATE TABLE IF NOT EXISTS price_trend (
  printing_id TEXT NOT NULL, gkey TEXT NOT NULL, game TEXT NOT NULL, label TEXT NOT NULL,
  n_sales INTEGER NOT NULL, n_30 INTEGER, last_sale TEXT, first_sale TEXT, sales_month REAL,
  median REAL, median_30 REAL, low REAL, high REAL, p25 REAL, p75 REAL,
  trend_label TEXT, trend_pct REAL, trend_ci_lo REAL, trend_ci_hi REAL, trend_why TEXT,
  change_90 REAL, change_why TEXT, change_span REAL, plain TEXT,
  short_move REAL, short_days INTEGER, move_label TEXT,
  ask_n INTEGER, ask_low REAL, ask_median REAL, ask_day TEXT, spread REAL,
  updated TEXT NOT NULL, PRIMARY KEY (printing_id, gkey));
CREATE INDEX IF NOT EXISTS trend_game ON price_trend(game);
CREATE INDEX IF NOT EXISTS trend_move ON price_trend(trend_label, n_sales);
"""


def label_for(pid: str) -> str:
    """A human label from the printing id itself, so a refresh never has to hold the whole catalog."""
    try:
        if pid.startswith("pk:"):
            _, _lang, card, tail = pid.split(":", 3)
            return f"{card} · {tail.split('@')[0].replace('-', ' ')}"
        body = pid.split(":", 1)[1]
        region, code, product, tail = body.split(":", 3)
        rarity = tail.split("@")[0].replace("-", " ")
        head = code if code and code != "-" else product.replace("-", " ")
        out = f"{head} · {rarity}"
        return out + (" · JP" if region.startswith("JP") else "")
    except ValueError:
        return pid


def _asks(st, now: datetime) -> dict[tuple[str, str], dict]:
    """The newest daily ask snapshot, and only that day: a listing counts as live now only if the snapshot still
    carries it. A dead listing last seen six days ago is not a price anyone can pay today."""
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ask_daily" not in have:
        return {}
    day = st.db.execute("SELECT MAX(day) FROM ask_daily").fetchone()[0]
    if not day or (now.date() - date.fromisoformat(day)).days > 3:
        return {}
    out: dict[tuple[str, str], dict] = {}
    for pid, g, low, med, n in st.db.execute(
            "SELECT printing_id, gkey, low, median, n FROM ask_daily WHERE day=?", (day,)):
        out[(pid, g or "|")] = {"day": day, "low": low, "median": med, "n": n}
    return out


def _pct(vals: list[float], q: float) -> float | None:
    if len(vals) < 3:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo = int(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def _short(points: list[tuple], now: datetime, *, days: int = 7, min_each: int = 3) -> float | None:
    """Week over the week before: median of the last `days` versus the `days` before that. None unless both sides
    have enough sales. Short history gets a short answer instead of waiting three months for a Theil-Sen slope."""
    cut = now - timedelta(days=days)
    prev = cut - timedelta(days=days)
    recent = [p for t, p in points if t >= cut and p > 0]
    before = [p for t, p in points if prev <= t < cut and p > 0]
    if len(recent) < min_each or len(before) < min_each:
        return None
    return (statistics.median(recent) / statistics.median(before) - 1) * 100


def refresh(st, now: datetime | None = None, home: str = "TWD") -> int:
    """Rebuild the board from stored sales. Returns the number of series written."""
    now = now or datetime.now(UTC)
    st.db.executescript(SCHEMA)
    cols = {r[1] for r in st.db.execute("PRAGMA table_info(price_trend)")}
    for c, t in (("short_move", "REAL"), ("short_days", "INTEGER"), ("move_label", "TEXT"), ("change_span", "REAL")):
        if c not in cols:
            st.db.execute(f"ALTER TABLE price_trend ADD COLUMN {c} {t}")
    pids = [r[0] for r in st.db.execute(
        "SELECT DISTINCT p.printing_id FROM observation_printing p JOIN observation o ON o.id=p.obs_id "
        "WHERE o.synthetic=0 AND o.evidence_type IN ('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION')")]
    if not pids:
        with st.db:
            st.db.execute("DELETE FROM price_trend")
        return 0
    groups = card_sales(st, pids)
    conv = Converter(st, home, home)
    asks = _asks(st, now)
    rows = []
    for (pid, gc, gr), obs in groups.items():
        points = []
        for o in obs:
            v = conv(o.original_amount, o.currency, o.event_time, fetch=False)
            if v is not None and v > 0:
                points.append((o.event_time, float(v)))
        if not points:
            continue
        points.sort()
        prices = [p for _, p in points]
        s = summarize(points, now)
        tr, ch = s["trend"], s["change_90"]
        m = _short(points, now)
        plain = plain_trend(s)
        if plain is None and m is not None:
            plain = ("Steady this week" if abs(m) < 5 else
                     f"{'Up' if m > 0 else 'Down'} {abs(m):.0f}% in the last week")
        tl = tr.get("label") if tr.get("label") in ("rising", "falling") else None
        move_label = tl or (("rising" if m >= 5 else "falling" if m <= -5 else "steady") if m is not None
                            else ("steady" if plain and plain.startswith("Steady") else None))
        span_days = max(1.0, (now - points[0][0]).total_seconds() / 86400)
        span_pct = None
        if len(prices) >= 6 and points[-1][0] > points[0][0]:
            k = min(3, len(prices) // 2)
            first, last = statistics.median(prices[:k]), statistics.median(prices[-k:])
            if first > 0:
                span_pct = (last / first - 1) * 100
        gkey = f"{gc}|{gr}"
        ask = asks.get((pid, gkey))
        med = statistics.median(prices)
        ask_now = (ask["low"] if ask.get("low") is not None else ask["median"]) if ask else None
        spread = (ask_now / med - 1) * 100 if ask_now and med else None
        rows.append((
            pid, gkey, "pkm" if pid.startswith("pk:") else "ygo", label_for(pid),
            len(points), s["n_30"], points[-1][0].isoformat(), points[0][0].isoformat(), len(points) / (span_days / 30.44),
            med, s["median_30"], min(prices), max(prices), _pct(prices, .25), _pct(prices, .75),
            tr.get("label"), tr.get("monthly_pct"), (tr.get("ci") or [None, None])[0], (tr.get("ci") or [None, None])[1],
            tr.get("why"), ch.get("pct"), ch.get("why"), span_pct, plain,
            m, 7 if m is not None else None, move_label,
            ask["n"] if ask else None, ask["low"] if ask else None, ask["median"] if ask else None, ask["day"] if ask else None,
            spread, now.isoformat()))
    with st.db:
        st.db.execute("DELETE FROM price_trend")
        st.db.executemany(
            "INSERT INTO price_trend (printing_id, gkey, game, label, n_sales, n_30, last_sale, first_sale, sales_month, "
            "median, median_30, low, high, p25, p75, trend_label, trend_pct, trend_ci_lo, trend_ci_hi, trend_why, "
            "change_90, change_why, change_span, plain, short_move, short_days, move_label, ask_n, ask_low, ask_median, ask_day, "
            "spread, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


_MOVE = "CASE WHEN trend_label IN ('rising','falling') THEN trend_label ELSE move_label END"
_SORTS = {"move": "ABS(COALESCE(change_90, 0)) DESC, ABS(COALESCE(trend_pct, 0)) DESC, ABS(COALESCE(short_move, 0)) DESC",
          "edge": "(COALESCE(median_30, median) - ask_low) / NULLIF(COALESCE(median_30, median), 0) DESC",
          "sales": "n_sales DESC, last_sale DESC",
          "spread": "ABS(COALESCE(spread, 0)) DESC",
          "recent": "last_sale DESC",
          "change": "ABS(COALESCE(change_90, 0)) DESC"}


def _ensure(st) -> bool:
    """Table present and current: columns added since the first release are filled in place."""
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "price_trend" not in have:
        return False
    cols = {r[1] for r in st.db.execute("PRAGMA table_info(price_trend)")}
    for c, t in (("short_move", "REAL"), ("short_days", "INTEGER"), ("move_label", "TEXT"), ("change_span", "REAL")):
        if c not in cols:
            st.db.execute(f"ALTER TABLE price_trend ADD COLUMN {c} {t}")
    return True


def board(st, *, game: str = "ygo", label: str | None = None, min_sales: int = 0,
          sort: str = "move", limit: int = 100) -> list[dict]:
    if not _ensure(st):
        return []
    where, args = ["game=?"], [game]
    if label in ("rising", "falling", "steady"):
        where.append(f"{_MOVE}=?")
        args.append(label)
    elif label == "none":
        where.append("plain IS NULL")
    if min_sales:
        where.append("n_sales>=?")
        args.append(min_sales)
    sql = f"SELECT * FROM price_trend WHERE {' AND '.join(where)} ORDER BY {_SORTS.get(sort, _SORTS['move'])} LIMIT ?"
    cols = [d[0] for d in st.db.execute("SELECT * FROM price_trend LIMIT 0").description]
    return [dict(zip(cols, r)) for r in st.db.execute(sql, (*args, int(limit)))]


def summary(st, game: str = "ygo") -> dict:
    if not _ensure(st):
        return {"series": 0, "rising": 0, "falling": 0, "steady": 0, "no_trend": 0, "n_30": 0, "with_asks": 0}
    row = st.db.execute(
        f"SELECT COUNT(*), SUM({_MOVE}='rising'), SUM({_MOVE}='falling'), SUM({_MOVE}='steady'), "
        f"SUM({_MOVE} IS NULL), COALESCE(SUM(n_30),0), SUM(ask_median IS NOT NULL) FROM price_trend WHERE game=?",
        (game,)).fetchone()
    return {"series": row[0] or 0, "rising": row[1] or 0, "falling": row[2] or 0, "steady": row[3] or 0,
            "no_trend": row[4] or 0, "n_30": row[5] or 0, "with_asks": row[6] or 0}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.trends")
    ap.add_argument("cmd", choices=["refresh", "top"])
    ap.add_argument("--store", default="data/store/evidence.sqlite")
    ap.add_argument("--home", default="TWD")
    ap.add_argument("--game", default="ygo", choices=["ygo", "pkm"])
    ap.add_argument("--label", default=None)
    ap.add_argument("--min-sales", type=int, default=0)
    ap.add_argument("--sort", default="move", choices=list(_SORTS))
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args(argv)
    from .store import Store
    st = Store(a.store)
    if a.cmd == "refresh":
        print(f"{refresh(st, datetime.now(UTC), a.home)} series written")
        return
    for r in board(st, game=a.game, label=a.label, min_sales=a.min_sales, sort=a.sort, limit=a.limit):
        move = f"{r['change_90']:+.0f}% 3mo" if r["change_90"] is not None else (r["plain"] or "no trend")
        ask = f" ask {r['ask_median']:,.0f} ({r['spread']:+.0f}%)" if r["ask_median"] else ""
        print(f"{r['label'][:52]:52} n={r['n_sales']:<3} med {r['median']:>9,.0f}  {move}{ask}")


if __name__ == "__main__":
    main()
