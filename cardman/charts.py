"""Server-rendered SVG / HTML charts for the review UI.

Colors are roles (CSS custom properties defined in ui.CSS), never raw hex here, so light and dark
mode swap in one place. Every mark that carries a value has a `data-tip` (read by ui.js with
textContent, so stored text is never parsed as HTML) and is focusable; every chart sits next to a
table holding the same numbers, so nothing is gated on hovering.
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass
from datetime import datetime, timedelta


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def fmt(v: float | None, cur: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"
    return f"{s} {cur}".strip()


def compact(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 10_000:
        return f"{v / 1000:.0f}K"
    return f"{v:,.0f}"


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def time_ticks(t0: datetime, t1: datetime, n: int = 6) -> list[tuple[datetime, str]]:
    span_h = (t1 - t0).total_seconds() / 3600
    if span_h <= 48:
        step = next(s for s in (1, 2, 3, 6, 12, 24) if span_h / s <= n)
        start = t0.astimezone().replace(minute=0, second=0, microsecond=0)
        out, t = [], start
        while t <= t1:
            if t >= t0 and t.hour % step == 0:
                full = span_h > 24 or t.hour == 0 or not out
                out.append((t, t.strftime("%b %d %H:%M" if full else "%H:%M")))
            t += timedelta(hours=1)
        return out
    days = span_h / 24
    step = next((s for s in (1, 2, 7, 14, 30, 61, 91, 182, 365) if days / s <= n), 730)
    start = t0.astimezone().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    out, t, last = [], start, None
    fmt_t = "%Y-%m" if step >= 30 else "%b %d"
    i = 0
    while t <= t1:
        if i % step == 0:
            lab = t.strftime(fmt_t)
            if lab != last:
                out.append((t, lab))
                last = lab
        t += timedelta(days=1)
        i += 1
    return out


# -- price over time ----------------------------------------------------------------------------

# Evidence families -> categorical slot (fixed order, validated all-pairs for 3 slots).
FAMILY = {
    "MARKETPLACE_MARKED_SOLD": ("sale", "circle"), "ACCEPTED_OFFER": ("sale", "diamond"),
    "INDEPENDENT_TRANSACTION": ("sale", "square"),
    "ACTIVE_ASKING": ("ask", "circle"), "OBSERVED_BID": ("ask", "diamond"), "AUCTION_ENDED_UNSOLD": ("ask", "square"),
    "AGGREGATE_ESTIMATE": ("other", "circle"), "SELLER_CLAIM": ("other", "diamond"),
}
FAMILY_VAR = {"sale": "--s1", "ask": "--s2", "other": "--s3"}
FAMILY_LABEL = {"sale": "Sales", "ask": "Asks and bids", "other": "Estimates and claims"}
SHAPE_LABEL = {"MARKETPLACE_MARKED_SOLD": "marked sold", "ACCEPTED_OFFER": "accepted offer",
               "INDEPENDENT_TRANSACTION": "independent sale", "ACTIVE_ASKING": "asking",
               "OBSERVED_BID": "bid", "AUCTION_ENDED_UNSOLD": "ended unsold",
               "AGGREGATE_ESTIMATE": "aggregate", "SELLER_CLAIM": "seller claim"}


@dataclass
class Point:  # noqa: D101
    t: datetime
    v: float
    evidence_type: str
    state: str           # "used" | "unused" | "void" (cancelled, refunded, invalid ...)
    tip: str
    _dx: float = 0.0


@dataclass
class Ref:
    label: str
    v: float
    role: str            # "ask" | "ceiling" | "exit"


def _shape(kind: str, x: float, y: float, r: float, attrs: str) -> str:
    if kind == "diamond":
        r *= 1.25
        return f'<path d="M{x:.1f} {y - r:.1f}L{x + r:.1f} {y:.1f}L{x:.1f} {y + r:.1f}L{x - r:.1f} {y:.1f}Z" {attrs}/>'
    if kind == "square":
        r *= 0.9
        return f'<rect x="{x - r:.1f}" y="{y - r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" rx="1.5" {attrs}/>'
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" {attrs}/>'


def price_chart(points: list[Point], refs: list[Ref], *, currency: str, valued_at: datetime | None = None,
                width: int = 1060, height: int = 300, label: str = "Price over time") -> str:
    if not points and not refs:
        return '<p class="muted">Nothing to plot yet.</p>'
    ml, mr, mt, mb = 60, 190, 14, 30
    pw, ph = width - ml - mr, height - mt - mb
    vals = [p.v for p in points] + [r.v for r in refs]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or max(abs(hi) * 0.1, 1)
    ticks = nice_ticks(lo - pad, hi + pad)
    y0, y1 = min(ticks[0], lo - pad), max(ticks[-1], hi + pad)
    times = [p.t for p in points] + ([valued_at] if valued_at else [])
    if times:
        t0, t1 = min(times), max(times)
    else:
        t1 = valued_at or datetime.now().astimezone()
        t0 = t1 - timedelta(days=30)
    span = (t1 - t0).total_seconds() or 86400 * 30
    t0 -= timedelta(seconds=span * 0.04)
    t1 += timedelta(seconds=span * 0.04)
    span = (t1 - t0).total_seconds()

    X = lambda t: ml + (t - t0).total_seconds() / span * pw
    Y = lambda v: mt + ph - (v - y0) / (y1 - y0) * ph

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" style="min-width:{width * 0.7:.0f}px" role="img" aria-label="{_e(label)}">']
    for tv in ticks:
        if y0 <= tv <= y1:
            out.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{Y(tv):.1f}" y2="{Y(tv):.1f}"/>'
                       f'<text class="tick" x="{ml - 8}" y="{Y(tv) + 4:.1f}" text-anchor="end">{_e(compact(tv))}</text>')
    out.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    # date ticks on whole days (or hours for very short spans), never repeating a label
    for t, lab in time_ticks(t0, t1):
        out.append(f'<line class="axis" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{mt + ph}" y2="{mt + ph + 4}"/>'
                   f'<text class="tick" x="{X(t):.1f}" y="{height - 8}" text-anchor="middle">{_e(lab)}</text>')
    if valued_at:
        xv = X(valued_at)
        out.append(f'<line class="axis" x1="{xv:.1f}" x2="{xv:.1f}" y1="{mt}" y2="{mt + ph}"/>'
                   f'<text class="tick" x="{xv - 4:.1f}" y="{mt + 10}" text-anchor="end">valued</text>')

    # reference lines with de-collided labels in the right margin
    refs_sorted = sorted(refs, key=lambda r: -r.v)
    placed: list[float] = []
    for r in refs_sorted:
        y = Y(r.v)
        ly = max(y, placed[-1] + 15) if placed else max(y, mt + 4)
        placed.append(ly)
    shift = max(0.0, (placed[-1] if placed else 0) - (mt + ph + 4))
    placed = [p - shift for p in placed]
    for r, ly in zip(refs_sorted, placed):
        y = Y(r.v)
        cls = {"ask": "ref-ask", "ceiling": "ref-ceiling"}.get(r.role, "ref-exit")
        tip = f"{fmt(r.v, currency)}\n{r.label}"
        out.append(f'<g data-tip="{_e(tip)}" tabindex="0">'
                   f'<line class="{cls}" x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<line class="hit" x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<path class="leader" d="M{ml + pw} {y:.1f}L{ml + pw + 8} {ly:.1f}L{ml + pw + 12} {ly:.1f}"/>'
                   f'<text class="{"rl strong" if r.role != "exit" else "rl"}" x="{ml + pw + 15}" y="{ly + 4:.1f}">'
                   f'{_e(r.label)} {_e(fmt(r.v))}</text></g>')

    # points: void first, then unused, then used on top
    order = {"void": 0, "unused": 1, "used": 2}
    taken: dict[tuple[int, int], int] = {}
    for p in sorted(points, key=lambda p: -order[p.state]):
        k = (round(X(p.t)), round(Y(p.v)))
        taken[k] = taken.get(k, -1) + 1
        p._dx = taken[k] * 9            # same date and price: step sideways so nothing hides
    for p in sorted(points, key=lambda p: order[p.state]):
        fam, shape = FAMILY.get(p.evidence_type, ("other", "circle"))
        x, y = X(p.t) + p._dx, Y(p.v)
        if p.state == "used":
            attrs = f'class="pt used" style="fill:var({FAMILY_VAR[fam]})"'
        elif p.state == "unused":
            attrs = f'class="pt unused" style="stroke:var({FAMILY_VAR[fam]})"'
        else:
            attrs = 'class="pt void"'
        mark = _shape(shape, x, y, 4.5, attrs)
        if p.state == "void":
            mark += f'<line class="void-x" x1="{x - 5:.1f}" y1="{y + 5:.1f}" x2="{x + 5:.1f}" y2="{y - 5:.1f}"/>'
        out.append(f'<g data-tip="{_e(p.tip)}" tabindex="0">{mark}'
                   f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="12"/></g>')
    out.append("</svg>")
    return '<div class="chartbox">' + "".join(out) + "</div>"


def price_legend(points: list[Point], refs: list[Ref]) -> str:
    fams = {FAMILY.get(p.evidence_type, ("other", "circle"))[0] for p in points}
    types = sorted({p.evidence_type for p in points}, key=lambda t: list(FAMILY).index(t) if t in FAMILY else 99)
    states = {p.state for p in points}
    items = []
    for t in types:
        fam, shape = FAMILY.get(t, ("other", "circle"))
        sw = _shape(shape, 7, 7, 4.5, f'style="fill:var({FAMILY_VAR[fam]})"')
        items.append(f'<span><svg width="14" height="14">{sw}</svg>{_e(FAMILY_LABEL[fam])}: {_e(SHAPE_LABEL.get(t, t))}</span>')
    if "unused" in states:
        items.append('<span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" class="pt unused" '
                     'style="stroke:var(--muted)"/></svg>hollow = not used as a comp</span>')
    if "void" in states:
        items.append('<span><svg width="14" height="14"><circle cx="7" cy="7" r="4.5" class="pt void"/>'
                     '<line class="void-x" x1="2" y1="12" x2="12" y2="2"/></svg>cancelled / refunded / invalid</span>')
    roles = {r.role for r in refs}
    if "ask" in roles:
        items.append('<span><svg width="18" height="14"><line class="ref-ask" x1="0" x2="18" y1="7" y2="7"/></svg>'
                     'listing ask</span>')
    if "ceiling" in roles:
        items.append('<span><svg width="18" height="14"><line class="ref-ceiling" x1="0" x2="18" y1="7" y2="7"/></svg>'
                     'buy ceiling</span>')
    if "exit" in roles:
        items.append('<span><svg width="18" height="14"><line class="ref-exit" x1="0" x2="18" y1="7" y2="7"/></svg>'
                     'exit scenarios</span>')
    if "median" in roles:
        items.append('<span><svg width="18" height="14"><line class="ref-exit" x1="0" x2="18" y1="7" y2="7"/></svg>'
                     'median of usable sales</span>')
    return f'<div class="legend">{"".join(items)}</div>' if fams or refs else ""


# -- part-to-whole: where the resale price goes ---------------------------------------------------

def split_bar(parts: list[tuple[str, float, str, str]], total: float, currency: str,
              marker: tuple[str, float] | None = None) -> str:
    """Tall segmented bar. parts: (name, amount, css var, explanation). marker: (label, value) on the same scale."""
    if total <= 0:
        return ""
    segs, legend = [], []
    for name, amt, var, why in parts:
        if amt <= 0:
            continue
        share = amt / total * 100
        inside = f'<span>{share:.0f}%</span>' if share >= 9 else ""
        tip = f"{fmt(amt, currency)} ({share:.1f}%)\n{name}\n{why}"
        segs.append(f'<div class="seg" style="flex:{amt:.4f} 0 0;background:var({var})" data-tip="{_e(tip)}" '
                    f'tabindex="0" aria-label="{_e(name)} {_e(fmt(amt, currency))}">{inside}</div>')
        legend.append(f'<tr><td><i class="key" style="background:var({var})"></i>{_e(name)}</td>'
                      f'<td class="n">{_e(fmt(amt, currency))}</td><td class="n muted">{share:.1f}%</td>'
                      f'<td class="muted">{_e(why)}</td></tr>')
    mk = ""
    if marker:
        pos = max(0.0, min(100.0, marker[1] / total * 100))
        over = marker[1] > total
        mk = (f'<div class="marker" style="left:{pos:.2f}%"><b></b>'
              f'<span class="{"right" if pos > 70 else ""}">{_e(marker[0])} {_e(fmt(marker[1], currency))}'
              f'{" (beyond the sale price)" if over else ""}</span></div>')
    return (f'<div class="split"><div class="bar">{"".join(segs)}</div>{mk}</div>'
            f'<table class="compact">{"".join(legend)}</table>')


# -- diverging horizontal bars (profit by scenario, ceiling sensitivity) -----------------------

def hbars(rows: list[tuple[str, float, str]], currency: str, *, width: int = 820, label: str = "",
          target: tuple[str, float] | None = None) -> str:
    """rows: (name, value, extra text). Bars grow from a shared zero; negatives use the red pole."""
    if not rows:
        return ""
    bh, gap, ml, mr = 18, 14, 210, 150
    pw = width - ml - mr
    vmax = max([abs(v) for _, v, _ in rows] + ([abs(target[1])] if target else [])) or 1
    has_neg = any(v < 0 for _, v, _ in rows)
    has_pos = any(v > 0 for _, v, _ in rows)
    if has_neg and has_pos:
        zx, scale = ml + pw / 2, pw / 2 / vmax
    elif has_neg:
        zx, scale = ml + pw, pw / vmax
    else:
        zx, scale = ml, pw / vmax
    top = 18 if target else 0
    h = len(rows) * (bh + gap) + 6 + top
    out = [f'<svg class="chart" viewBox="0 0 {width} {h}" style="min-width:{width * 0.7:.0f}px" role="img" aria-label="{_e(label)}">']
    for i, (name, v, extra) in enumerate(rows):
        y = 4 + top + i * (bh + gap)
        w = abs(v) * scale
        x = zx - w if v < 0 else zx
        r = min(4, w / 2)
        var = "--neg" if v < 0 else "--s1"
        # square at the baseline, rounded at the data end
        if v < 0:
            d = (f"M{zx:.1f} {y}H{x + r:.1f}Q{x:.1f} {y} {x:.1f} {y + r:.1f}V{y + bh - r:.1f}"
                 f"Q{x:.1f} {y + bh} {x + r:.1f} {y + bh}H{zx:.1f}Z")
        else:
            xe = zx + w
            d = (f"M{zx:.1f} {y}H{xe - r:.1f}Q{xe:.1f} {y} {xe:.1f} {y + r:.1f}V{y + bh - r:.1f}"
                 f"Q{xe:.1f} {y + bh} {xe - r:.1f} {y + bh}H{zx:.1f}Z")
        sign = "+" if v > 0 else ("−" if v < 0 else "")
        val = f"{sign}{fmt(abs(v), currency)}"
        text = val + (f" · {extra}" if extra else "")
        est = len(text) * 6.6
        tcls = "rl strong"
        if v < 0:
            tx, anchor = x - 6, "end"
            if tx - est < 4:                       # no room left of the bar: write inside it
                tx, anchor, tcls = x + 8, "start", "rl inbar"
        else:
            tx, anchor = zx + w + 6, "start"
            if tx + est > width - 4:
                tx, anchor, tcls = zx + w - 8, "end", "rl inbar"
        tip = f"{val}\n{name}" + (f"\n{extra}" if extra else "")
        out.append(f'<g data-tip="{_e(tip)}" tabindex="0">'
                   f'<text class="rl" x="{ml - 10}" y="{y + 13}" text-anchor="end">{_e(name)}</text>'
                   + (f'<path d="{d}" style="fill:var({var})"/>' if w > 0.5 else "")
                   + f'<rect class="hit-r" x="{ml}" y="{y - 4}" width="{pw}" height="{bh + 8}"/>'
                   f'<text class="{tcls}" x="{tx:.1f}" y="{y + 13}" text-anchor="{anchor}">{_e(text)}</text></g>')
    out.append(f'<line class="axis" x1="{zx:.1f}" x2="{zx:.1f}" y1="{top}" y2="{h}"/>')
    if target:
        tx = zx + target[1] * scale
        out.insert(1, f'<line class="ref-ceiling" x1="{tx:.1f}" x2="{tx:.1f}" y1="{top - 2}" y2="{h}"/>'
                      f'<text class="rl strong" x="{tx:.1f}" y="12" text-anchor="middle">'
                      f'{_e(target[0])} {_e(fmt(target[1], currency))}</text>')
    out.append("</svg>")
    return '<div class="chartbox">' + "".join(out) + "</div>"


# -- small inline figures ------------------------------------------------------------------------

def room_bar(room_pct: float | None, *, width: int = 120) -> str:
    """Ask vs ceiling as a diverging bar around zero, clipped at ±50%."""
    if room_pct is None:
        return '<span class="muted">no ceiling</span>'
    half = width / 2
    v = max(-50.0, min(50.0, room_pct))
    w = abs(v) / 50 * (half - 2)
    x = half if v >= 0 else half - w
    var = "--s1" if v >= 0 else "--neg"
    word = "under" if room_pct >= 0 else "over"
    return (f'<span class="room" data-tip="{_e(f"{abs(room_pct):.1f}% {word} the ceiling")}" tabindex="0">'
            f'<svg width="{width}" height="12" aria-hidden="true"><rect x="0" y="5" width="{width}" height="2" '
            f'class="track"/><rect x="{x:.1f}" y="1" width="{max(w, 1.5):.1f}" height="10" rx="2" '
            f'style="fill:var({var})"/><line class="axis" x1="{half}" x2="{half}" y1="0" y2="12"/></svg>'
            f'<span>{"+" if room_pct >= 0 else "−"}{abs(room_pct):.0f}%</span></span>')


def count_bar(n: int, need: int | None, scale: int) -> str:
    """Count of usable comps against the minimum needed."""
    width = 140
    w = min(n, scale) / scale * width
    var = "--s1" if need is None or n >= need else "--muted"
    return (f'<svg width="{width + 2}" height="12" aria-hidden="true"><rect x="0" y="5" width="{width}" height="2" '
            f'class="track"/><rect x="0" y="1" width="{max(w, 0):.1f}" height="10" rx="2" style="fill:var({var})"/>'
            + (f'<line class="need" x1="{need / scale * width:.1f}" x2="{need / scale * width:.1f}" y1="0" y2="12"/>'
               if need is not None else "") + '</svg>')
