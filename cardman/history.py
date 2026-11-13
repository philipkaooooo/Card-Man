"""Price history of one card: every real sale, weekly and monthly buckets, asks per day, guide prices, honest trend.

Rules (see docs/pricing-method.md, "History and charts"):
* One series per exact printing and grade: (printing, grade company, grade). Raw is its own series. Never pooled.
* A sale is shown once: lineage dedupe, then the one-to-one cross-source match; excluded flags (duplicates, relists,
  gamed shipping) are left out of the chart too.
* A sale without a real date (Mercari search pages, rows whose page showed no date) is counted, not charted.
* Each sale is converted to the display currency once, at the rates of its own sale date. No rate -> not charted.
* Buckets are calendar weeks (ISO, Monday start) and months, in UTC. Empty buckets stay empty: no interpolation.
  The P25-P75 band is given only where a bucket has 3+ sales.
* Trend: Theil-Sen slope of log price per month on the individual sales, with a seeded bootstrap 95% interval.
  Labelled rising or falling only when the interval excludes zero (6+ sales over 30+ days); otherwise "no clear trend".
* 90-day change: median of the last 30 days vs median of days 90-120 ago, only when both have 3+ sales.
* Asks are a separate series (a daily snapshot of live listings sure to be this printing), never mixed with sales.
"""
from __future__ import annotations

import hashlib
import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .evidence import dedup
from .fx import FxTable, StaleRateError
from .models import COMP_ELIGIBLE
from .verify import EXCLUDING, assess

UTC = timezone.utc
SOLD_TYPES = ("MARKETPLACE_MARKED_SOLD", "ACCEPTED_OFFER", "INDEPENDENT_TRANSACTION")
ASK_CARD_PROB, ASK_PRINTING_PROB = 0.95, 0.9
SITE_OF = (("ebay_browse", "eBay"), ("ebay", "eBay"), ("130point", "eBay"), ("yahoo", "Yahoo! Auctions"),
           ("mercari", "Mercari"), ("manual", "You"))


def site_label(source: str) -> str:
    s = (source or "").lower()
    return next((lab for k, lab in SITE_OF if s.startswith(k)), source or "other")


# -- buckets ----------------------------------------------------------------------------------------------------------

def week_start(t: datetime) -> date:
    d = t.astimezone(UTC).date()
    return d - timedelta(days=d.weekday())


def month_start(t: datetime) -> date:
    return t.astimezone(UTC).date().replace(day=1)


def _pct(vals: list[float], q: float) -> float:
    """Linear interpolation between order statistics (the 'inclusive' definition)."""
    s = sorted(vals)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def buckets(points: list[tuple[datetime, float]], unit: str) -> list[dict]:
    """Only buckets that hold sales, in time order. A missing bucket means no sale, and is not filled in."""
    start = week_start if unit == "week" else month_start
    groups: dict[date, list[float]] = {}
    for t, p in points:
        groups.setdefault(start(t), []).append(p)
    out = []
    for d in sorted(groups):
        v = groups[d]
        out.append({"start": d.isoformat(), "n": len(v), "median": statistics.median(v), "low": min(v), "high": max(v),
                    "p25": _pct(v, .25) if len(v) >= 3 else None, "p75": _pct(v, .75) if len(v) >= 3 else None})
    return out


# -- trend and change -------------------------------------------------------------------------------------------------

def _sen(xs: list[float], ys: list[float]) -> float | None:
    sl = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i]]
    return statistics.median(sl) if sl else None


def trend(points: list[tuple[datetime, float]], *, min_n: int = 6, min_span_days: float = 30, boots: int = 400) -> dict:
    pts = [(t, p) for t, p in points if p > 0]
    if len(pts) < min_n:
        return {"label": "no clear trend", "why": f"needs {min_n}+ sales, has {len(pts)}"}
    t0 = min(t for t, _ in pts)
    xs = [(t - t0).total_seconds() / 86400 / 30.0 for t, _ in pts]
    ys = [math.log(p) for _, p in pts]
    if (max(xs) - min(xs)) * 30 < min_span_days:
        return {"label": "no clear trend", "why": f"sales span under {min_span_days:.0f} days"}
    slope = _sen(xs, ys)
    if slope is None:
        return {"label": "no clear trend", "why": "all sales on one day"}
    seed = int(hashlib.sha256(repr(sorted(zip(xs, ys))).encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    idx = range(len(xs))
    bs = []
    for _ in range(boots):
        pick = [rng.choice(idx) for _ in idx]
        s = _sen([xs[i] for i in pick], [ys[i] for i in pick])
        if s is not None:
            bs.append(s)
    bs.sort()
    lo, hi = bs[int(.025 * (len(bs) - 1))], bs[int(.975 * (len(bs) - 1))]
    pct = lambda s: (math.exp(s) - 1) * 100
    label = "rising" if lo > 0 else "falling" if hi < 0 else "no clear trend"
    return {"label": label, "monthly_pct": pct(slope), "ci": [pct(lo), pct(hi)], "n": len(pts),
            "why": f"{pct(slope):+.1f}% a month, 95% interval {pct(lo):+.1f}% to {pct(hi):+.1f}%"}


def change_90(points: list[tuple[datetime, float]], now: datetime) -> dict:
    recent = [p for t, p in points if now - timedelta(days=30) <= t <= now]
    then = [p for t, p in points if now - timedelta(days=120) <= t < now - timedelta(days=90)]
    if len(recent) < 3 or len(then) < 3:
        return {"pct": None, "why": f"not enough sales (last 30 days: {len(recent)}, 90-120 days ago: {len(then)}; need 3 each)"}
    a, b = statistics.median(then), statistics.median(recent)
    return {"pct": (b / a - 1) * 100, "why": f"median {b:,.2f} (n={len(recent)}) vs {a:,.2f} (n={len(then)})"}


def summarize(points: list[tuple[datetime, float]], now: datetime) -> dict:
    last30 = [p for t, p in points if t >= now - timedelta(days=30)]
    return {"median_30": statistics.median(last30) if len(last30) >= 3 else None, "n_30": len(last30),
            "change_90": change_90(points, now), "trend": trend(points)}


# -- currency ---------------------------------------------------------------------------------------------------------

class Converter:
    """amount in `cur` at moment t -> display currency, via the home currency, both legs at the same date's rates."""
    _tried: set = set()

    def __init__(self, st, home: str, display: str, fx_store=None):
        self.st, self.home, self.display, self.fx_store = st, home, display, fx_store
        self.table = FxTable(st.fx_rates(), max_age=timedelta(days=3))

    def _ensure(self, cur: str, t: datetime) -> None:
        if self.fx_store is None or cur == self.home:
            return
        k = (str(self.st.path), cur, self.home, t.astimezone(UTC).date())
        if k in Converter._tried:
            return
        Converter._tried.add(k)
        try:
            self.fx_store.rates_for([(cur, self.home)], [t])
            self.table = FxTable(self.st.fx_rates(), max_age=timedelta(days=2))
        except Exception:
            pass

    def __call__(self, amount, cur: str, t: datetime, *, fetch: bool = True) -> float | None:
        """fetch=False: stored rates only (asks and guide prices, one per day, need no exact-day backfill)."""
        if cur == self.display:
            return float(amount)
        try:
            for c in ({cur, self.display} - {self.home}) if fetch else ():
                self._ensure(c, t)
            home = Decimal(str(amount)) if cur == self.home else self.table.convert(Decimal(str(amount)), cur, self.home, t)
            return float(home if self.display == self.home else self.table.convert(home, self.home, self.display, t))
        except (StaleRateError, Exception):
            return None


# -- collecting sales -------------------------------------------------------------------------------------------------

def _undated(o) -> bool:
    return not o.source.startswith("ebay_browse") and o.event_time == o.capture_time


def series_key(o) -> tuple[str, str, str]:
    return (o.printing_candidates[0], o.grade_company or "", o.grade or "")


def card_sales(st, printing_ids: list[str]) -> dict[tuple, list]:
    """Real, single-printing, single-copy sales per (printing, grade company, grade), duplicates and excluded removed."""
    ids = []
    for i in range(0, len(printing_ids), 500):
        chunk = printing_ids[i:i + 500]
        ids += [r[0] for r in st.db.execute(
            "SELECT DISTINCT o.id FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
            f"o.evidence_type IN {SOLD_TYPES} AND p.printing_id IN ({','.join('?' * len(chunk))})", chunk)]
    groups: dict[tuple, list] = {}
    for oid in ids:
        try:
            o = st.effective(oid)
        except Exception:
            continue
        if (o.status != "VALID" or len(o.printing_candidates) != 1 or o.is_lot or o.quantity != 1
                or o.evidence_type not in COMP_ELIGIBLE or o.original_amount <= 0):
            continue
        groups.setdefault(series_key(o), []).append(o)
    out = {}
    for k, obs in groups.items():
        kept, _ = dedup(obs)
        flags = assess(kept, datetime.max.replace(tzinfo=UTC))
        out[k] = [o for o in kept if not (o.id in flags and set(flags[o.id].flags) & EXCLUDING)]
    return out


# -- asks: a daily snapshot per printing ------------------------------------------------------------------------------

ASK_SCHEMA = """CREATE TABLE IF NOT EXISTS ask_daily (printing_id TEXT NOT NULL, gkey TEXT NOT NULL, day TEXT NOT NULL,
  low REAL, median REAL, n INTEGER NOT NULL, currency TEXT NOT NULL, PRIMARY KEY (printing_id, gkey, day));"""


def update_ask_daily(st, now: datetime, home: str = "TWD") -> int:
    """Write one row per printing and grade for every day not yet stored, and rewrite today. Past days stay as written.
    A listing counts for a printing only when the engine is 95%+ sure of the card and 90%+ of the printing."""
    import json
    st.db.executescript(ASK_SCHEMA)
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"card_listing", "listing"} <= have:
        return 0
    rows = st.db.execute(
        "SELECT c.printing_id, l.price, l.currency, l.first_seen, l.last_seen, l.ended_at, l.data_json FROM card_listing c "
        "JOIN listing l ON l.key=c.key WHERE c.card_prob>=? AND c.printing_prob>=? AND c.lot=0 AND l.kind='ask' AND c.status<>'REJECTED'",
        (ASK_CARD_PROB, ASK_PRINTING_PROB)).fetchall()
    if not rows:
        return 0
    first = min(date.fromisoformat(r[3][:10]) for r in rows if r[3])
    today = now.astimezone(UTC).date()
    done = {r[0] for r in st.db.execute("SELECT DISTINCT day FROM ask_daily")}
    days = [first + timedelta(days=i) for i in range((today - first).days + 1)]
    days = [d for d in days if d.isoformat() not in done or d == today]
    table = FxTable(st.fx_rates(), max_age=timedelta(days=3))
    out = []
    for d in days:
        per: dict[tuple, list[float]] = {}
        at = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
        for pid, price, cur, fs, ls, ended, dj in rows:
            if not fs or fs[:10] > d.isoformat() or (ls and ls[:10] < d.isoformat()) or (ended and ended[:10] < d.isoformat()):
                continue
            try:
                v = float(price) if cur == home else float(table.convert(Decimal(str(price)), cur, home, min(at, now)))
            except (StaleRateError, Exception):
                continue
            data = json.loads(dj or "{}")
            per.setdefault((pid, f'{data.get("grade_company") or ""}|{data.get("grade") or ""}'), []).append(v)
        for (pid, g), vals in per.items():
            out.append((pid, g, d.isoformat(), min(vals), statistics.median(vals), len(vals), home))
    with st.db:
        st.db.execute("DELETE FROM ask_daily WHERE day=?", (today.isoformat(),))
        st.db.executemany("INSERT OR REPLACE INTO ask_daily VALUES (?,?,?,?,?,?,?)", out)
    return len(out)


# -- the card's history ---------------------------------------------------------------------------------------------

@dataclass
class Variant:
    id: str
    label: str
    set_code: str | None = None
    rarity: str | None = None


def build(st, variants: list, now: datetime, conv: Converter, guide_fn=None) -> dict:
    """guide_fn: optional printing id -> [(day, usd)] (Pokémon: the TCGplayer market price of that version, a guide only)."""
    """Everything the chart and the text table show, in the display currency."""
    lab = {v.id: " · ".join(x for x in (getattr(v, "set_code", None) or "no code", getattr(v, "rarity", None),
                                         getattr(v, "edition", None)) if x) for v in variants}
    by_id = {v.id: v for v in variants}
    sales = card_sales(st, list(by_id))
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    asks: dict[tuple, list] = {}
    if "ask_daily" in have and by_id:
        ids = list(by_id)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for pid, g, day, low, med, n, cur in st.db.execute(
                    f"SELECT printing_id, gkey, day, low, median, n, currency FROM ask_daily WHERE printing_id IN ({','.join('?' * len(chunk))}) "
                    "ORDER BY day", chunk):
                at = datetime.fromisoformat(day).replace(hour=12, tzinfo=UTC)
                lo, md = conv(low, cur, min(at, now), fetch=False), conv(med, cur, min(at, now), fetch=False)
                if lo is not None:
                    gc, gr = (g.split("|") + [""])[:2]
                    asks.setdefault((pid, gc, gr), []).append({"d": day, "low": lo, "median": md, "n": n})
    open_printing = 0
    ids_ = list(by_id)
    for i in range(0, len(ids_), 500):
        chunk = ids_[i:i + 500]
        open_printing += st.db.execute(
            "SELECT COUNT(DISTINCT o.id) FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
            f"o.evidence_type IN {SOLD_TYPES} AND p.printing_id IN ({','.join('?' * len(chunk))}) AND "
            "(SELECT COUNT(*) FROM observation_printing q WHERE q.obs_id=o.id) > 1", chunk).fetchone()[0]
    depth: dict[str, str] = {}
    undated = 0
    series = []
    for key in sorted(set(sales) | set(asks), key=lambda k: (-len(sales.get(k, [])), -len(asks.get(k, [])), k)):
        pid, gc, gr = key
        pts, dots = [], []
        for o in sorted(sales.get(key, []), key=lambda o: o.event_time):
            site = site_label(o.source)
            if _undated(o):
                undated += 1
                continue
            p = conv(o.original_amount, o.currency, o.event_time)
            if p is None:
                continue
            depth[site] = min(depth.get(site, o.event_time.date().isoformat()), o.event_time.date().isoformat())
            pts.append((o.event_time, p))
            dots.append({"t": o.event_time.astimezone(UTC).isoformat(), "p": round(p, 2), "site": site,
                         "cond": " ".join(x for x in (o.grade_company, o.grade) if x) or (o.observed_condition or "raw"),
                         "orig": f"{o.original_amount} {o.currency}" if o.currency != conv.display else None})
        guide = []
        v = by_id.get(pid)
        if not gc and guide_fn is not None and pid.startswith("pk:"):
            for day, usd in guide_fn(pid):
                g = conv(Decimal(str(usd)), "USD", min(datetime.fromisoformat(day).replace(hour=12, tzinfo=UTC), now), fetch=False)
                if g is not None:
                    guide.append({"d": day, "v": round(g, 2)})
        elif not gc and v is not None and getattr(v, "set_code", None) and "reference_price" in have:
            for day, usd, rar in st.db.execute("SELECT day, usd, rarity FROM reference_price WHERE code=? AND usd<>'' ORDER BY day",
                                               (v.set_code,)):
                if (rar or "").lower() == (getattr(v, "rarity", "") or "").lower():
                    g = conv(Decimal(usd), "USD", min(datetime.fromisoformat(day).replace(hour=12, tzinfo=UTC), now), fetch=False)
                    if g is not None:
                        guide.append({"d": day, "v": round(g, 2)})
        series.append({"key": "|".join(key), "printing_id": pid, "label": lab.get(pid, pid),
                       "grade": " ".join(x for x in (gc, gr) if x) or "Raw", "sales": dots,
                       "week": buckets(pts, "week"), "month": buckets(pts, "month"), "asks": asks.get(key, []), "guide": guide,
                       "summary": {**summarize(pts, now), "last": dots[-1] if dots else None, "n": len(dots)}})
    return {"currency": conv.display, "now": now.astimezone(UTC).isoformat(), "series": series,
            "default": series[0]["key"] if series else None, "depth": depth, "undated": undated, "printing_open": open_printing}


def sparkline(weeks: list[dict], now: datetime, *, w: int = 72, h: int = 20) -> str:
    """Weekly sold medians over the last 90 days as a tiny line; gaps stay gaps. Only with 3+ sales in the window."""
    start = now.astimezone(UTC).date() - timedelta(days=90)
    pts = [b for b in weeks if date.fromisoformat(b["start"]) >= start - timedelta(days=6)]
    if sum(b["n"] for b in pts) < 3 or len(pts) < 2:
        return ""
    lo, hi = min(b["median"] for b in pts), max(b["median"] for b in pts)
    span = (hi - lo) or 1.0
    x = lambda d: 1 + (w - 2) * max(0, (date.fromisoformat(d) - start).days) / 90
    y = lambda v: h - 2 - (h - 4) * (v - lo) / span
    segs, cur, prev = [], [], None
    for b in pts:
        d = date.fromisoformat(b["start"])
        if prev is not None and (d - prev).days > 7:
            segs.append(cur)
            cur = []
        cur.append(f"{x(b['start']):.1f},{y(b['median']):.1f}")
        prev = d
    segs.append(cur)
    lines = "".join(f'<polyline points="{" ".join(s)}"/>' if len(s) > 1 else f'<circle cx="{s[0].split(",")[0]}" cy="{s[0].split(",")[1]}" r="1.5"/>'
                    for s in segs if s)
    n = sum(b["n"] for b in pts)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="90-day sold median, {n} sales">'
            f'<title>90-day weekly sold median ({n} sales)</title>{lines}</svg>')


_SPARK_CACHE: dict = {}


def sparks(st, now: datetime, home: str = "TWD") -> dict[str, str]:
    """Raw-series sparkline per printing id, for search results and listing rows. Cached until sales change."""
    marker = st.db.execute(f"SELECT COUNT(*), MAX(recorded_at) FROM observation WHERE synthetic=0 AND evidence_type IN {SOLD_TYPES}").fetchone()
    k = (str(st.path), marker, now.date())
    if k in _SPARK_CACHE:
        return _SPARK_CACHE[k]
    pids = [r[0] for r in st.db.execute(
        "SELECT DISTINCT p.printing_id FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
        f"o.evidence_type IN {SOLD_TYPES}")]
    conv = Converter(st, home, home)
    out = {}
    for key, obs in card_sales(st, pids).items():
        if key[1]:
            continue
        pts = [(o.event_time, p) for o in obs if not _undated(o) for p in [conv(o.original_amount, o.currency, o.event_time)] if p is not None]
        svg = sparkline(buckets(pts, "week"), now)
        if svg:
            out[key[0]] = svg
    _SPARK_CACHE.clear()
    _SPARK_CACHE[k] = out
    return out


_BUILD_CACHE: dict = {}


def cached_build(st, passcode: str, variants: list, now: datetime, conv: Converter, guide_fn=None) -> dict:
    """build() memoized until sales, asks, guide prices or rates change (and at most for the day)."""
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    m = [st.db.execute(f"SELECT COUNT(*), MAX(recorded_at) FROM observation WHERE synthetic=0 AND evidence_type IN {SOLD_TYPES}").fetchone(),
         st.db.execute("SELECT COUNT(*), MAX(as_of) FROM fx_rate").fetchone()]
    for t, q in (("ask_daily", "SELECT COUNT(*), MAX(day), SUM(n) FROM ask_daily"), ("reference_price", "SELECT COUNT(*), MAX(day) FROM reference_price")):
        m.append(st.db.execute(q).fetchone() if t in have else None)
    k = (str(st.path), passcode, conv.display, repr(m), now.astimezone(UTC).date())
    if k not in _BUILD_CACHE:
        if len(_BUILD_CACHE) > 64:
            _BUILD_CACHE.clear()
        _BUILD_CACHE[k] = build(st, variants, now, conv, guide_fn=guide_fn)
    return _BUILD_CACHE[k]


def plain_trend(summary: dict) -> str | None:
    """'Up 12% in 3 months', 'Down 8% in 3 months' or 'Steady', only when the sales support it; otherwise None."""
    ch = (summary.get("change_90") or {}).get("pct")
    if ch is not None:
        return "Steady over 3 months" if abs(ch) < 3 else f'{"Up" if ch > 0 else "Down"} {abs(ch):.0f}% in 3 months'
    t = summary.get("trend") or {}
    if t.get("label") in ("rising", "falling"):
        three = ((1 + t["monthly_pct"] / 100) ** 3 - 1) * 100
        return f'{"Up" if three > 0 else "Down"} about {abs(three):.0f}% in 3 months'
    if t.get("ci") and -3 <= t["ci"][0] and t["ci"][1] <= 3:
        return "Steady"
    return None


def first_seen(series: list[dict]) -> str | None:
    """The first day CardMan holds anything for these series (a sale, an ask or a guide price)."""
    days = [d["t"][:10] for s in series for d in s["sales"]] + [a["d"] for s in series for a in s["asks"]] + [g["d"] for s in series for g in s["guide"]]
    return min(days) if days else None
