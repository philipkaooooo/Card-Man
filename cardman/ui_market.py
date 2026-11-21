"""Market board: the whole catalog's price history, one line per printing and grade.

Nothing here is an estimate of its own: every median, trend and change comes from the same sold evidence the
card pages use, already deduped and condition-checked. Live asks are shown beside the sold price as a separate
series, never mixed in. A printing with no real sales does not appear.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from . import trends
from .games import card_href
from .ui_home import Money, e, passcode_of, _tables

MARKET_CSS = """
.move-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:6px 0 14px}
.move-grid h3{margin:0 0 4px;font-size:13px;color:var(--fg2);text-transform:uppercase;letter-spacing:.04em}
ul.mini{list-style:none;margin:0;padding:0}ul.mini li{padding:3px 0;font-size:13px;border-bottom:1px solid var(--line)}
ul.mini li:last-child{border-bottom:0}.up{color:var(--teal-deep);font-weight:600}.down{color:#b3261e;font-weight:600}
@media(max-width:760px){.move-grid{grid-template-columns:1fr}}
"""


def _grade(gkey: str) -> str:
    gc, _, gr = (gkey or "|").partition("|")
    return f"{gc} {gr}".strip() if gc else ""


def _chips(qs: dict, values: dict) -> str:
    out = []
    for key, options in values.items():
        for val, text in options:
            q = {k: v for k, v in qs.items() if k not in ("msg", "err")}
            if val in (None, "all", ""):
                q.pop(key, None)
            else:
                q[key] = val
            on = (qs.get(key) or vals_default(key)) == val
            out.append(f'<a class="fchip{" on" if on else ""}" href="/market?{e(urlencode(q))}">{e(text)}</a>')
    return '<div class="fchips">' + "".join(out) + "</div>"


def vals_default(key: str) -> str:
    return {"label": "all", "sort": "move", "min": "0"}.get(key, "")


def view_market(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo") -> str:
    M = Money(st, home, cur, now)
    if "price_trend" not in _tables(st):
        return ('<h1>Market</h1><p class="empty">No sales are stored yet. The engine writes the board at the end of every '
                'pass, or run <code>python -m cardman.trends refresh</code>.</p>')
    label = qs.get("label", ["all"])[0]
    label = label if label in ("all", "rising", "falling", "steady", "none") else "all"
    sort = qs.get("sort", ["move"])[0]
    sort = sort if sort in ("move", "sales", "spread", "recent") else "move"
    min_sales = qs.get("min", ["0"])[0]
    min_sales = int(min_sales) if min_sales.isdigit() else 0
    s = trends.summary(st, game)
    rows = trends.board(st, game=game, label=None if label == "all" else label, min_sales=min_sales, sort=sort, limit=200)
    name = {"ygo": "Yu-Gi-Oh!", "pkm": "Pokémon"}.get(game, game)
    head = (f'<h1>{e(name)} market</h1>'
            f'<p class="sub">Every version with real sales behind it. Median and trend come from those sales only; '
            f'live asks are the separate column on the right.</p>'
            '<div class="stats">'
            f'<div class="stat"><small>Versions priced</small><b>{s["series"]:,}</b></div>'
            f'<div class="stat"><small>Rising</small><b>{s["rising"]:,}</b></div>'
            f'<div class="stat"><small>Falling</small><b>{s["falling"]:,}</b></div>'
            f'<div class="stat"><small>Steady</small><b>{s["steady"]:,}</b></div>'
            f'<div class="stat"><small>Sales, last 30 days</small><b>{s["n_30"]:,}</b></div>'
            f'<div class="stat"><small>With live asks</small><b>{s["with_asks"]:,}</b></div></div>')
    chips = _chips({**qs, "sort": sort, "min": str(min_sales)}, {
        "label": [("all", "Every version"), ("rising", "Rising"), ("falling", "Falling"), ("steady", "Steady"), ("none", "No trend yet")],
        "min": [("0", "Any sales count"), ("3", "3+ sales"), ("6", "6+ sales")],
        "sort": [("move", "Biggest move"), ("sales", "Most sales"), ("spread", "Widest ask gap"), ("recent", "Newest sale")],
    })
    if not rows:
        return (head + chips + '<p class="empty">No version matches these filters yet. A version appears once real sales of '
                'that exact printing and grade are stored.</p>')
    body = []
    for r in rows:
        pid, gkey = r["printing_id"], r["gkey"] or "|"
        pc = passcode_of(pid)
        name_cell = f'<a href="{e(card_href(pc))}">{e(r["label"])}</a>' if pc else e(r["label"])
        grade = _grade(gkey)
        move = r["plain"] or "—"
        if r["change_90"] is not None:
            move = f'{move} <span class="muted small">{r["change_90"]:+.0f}% 3 mo</span>'
        ask = M(r["ask_median"]) if r["ask_median"] is not None else "—"
        gap = (f'{r["spread"]:+.0f}%' if r["spread"] is not None else "—")
        why = e(r["trend_why"] or r["change_why"] or
                ("median of the last 7 days against the 7 before it" if r.get("short_move") is not None
                 else "not enough dated sales for a trend"))
        body.append(
            f'<tr><td>{name_cell}{f" <span class=muted>{e(grade)}</span>" if grade else ""}'
            f'<br><span class="muted small">{e(r["label"].split(" · ")[0])}</span></td>'
            f'<td class="n">{r["n_sales"]}{f"<br><span class=muted small>{r['n_30']} last 30d</span>" if r["n_30"] else ""}</td>'
            f'<td class="n">{M(r["median"])}</td>'
            f'<td title="{why}">{e(move)}</td>'
            f'<td class="n">{e(r["last_sale"][:10]) if r["last_sale"] else "—"}</td>'
            f'<td class="n">{ask}</td><td class="n">{gap}</td></tr>')
    table = ('<div class="wrap"><table class="compact"><tr><th>Printing</th><th class="n">Sales</th><th class="n">Sold median</th>'
             '<th>Trend</th><th class="n">Last sale</th><th class="n">Lowest ask</th><th class="n">Ask vs sold</th></tr>'
             + "".join(body) + '</table></div>')
    note = ('<p class="muted small">Sold median is in your display currency, converted at each sale\'s own date. '
            'A 3-month trend is Theil-Sen on the individual sales; while the history is younger than that, the Trend column '
            'shows the last 7 days against the 7 before, if both sides have at least 3 sales. "—" means neither is supported yet. '
            '"Ask vs sold" compares the lowest current ask with the sold median: positive means the market is asking above what copies sell for.</p>')
    return head + chips + table + note


def movement_strip(st, game: str, M: Money) -> str:
    """A short row on Home: what moved, and how much of the catalog has sales behind it."""
    if "price_trend" not in _tables(st):
        return ""
    s = trends.summary(st, game)
    if not s["series"]:
        return ""
    up = trends.board(st, game=game, label="rising", min_sales=3, sort="change", limit=4)
    down = trends.board(st, game=game, label="falling", min_sales=3, sort="change", limit=4)

    def li(r):
        pc = passcode_of(r["printing_id"])
        who = f'<a href="{e(card_href(pc))}">{e(r["label"])}</a>' if pc else e(r["label"])
        ch = r["change_90"] if r["change_90"] is not None else (
            (r["trend_pct"] or 0) * 3 if r["trend_pct"] is not None else (r.get("short_move") or 0))
        return f'<li>{who} <span class="{"up" if ch > 0 else "down"}">{ch:+.0f}%</span> <span class="muted small">n={r["n_sales"]}</span></li>'

    blocks = []
    if up:
        blocks.append('<div><h3>Rising</h3><ul class="mini">' + "".join(li(r) for r in up) + "</ul></div>")
    if down:
        blocks.append('<div><h3>Falling</h3><ul class="mini">' + "".join(li(r) for r in down) + "</ul></div>")
    if not blocks:
        blocks.append('<p class="empty">No version has enough dated sales for a direction yet.</p>')
    return ('<div class="home-h"><h2>Market movement</h2><a href="/market">Whole market →</a></div>'
            f'<p class="muted small">{s["series"]:,} versions priced from real sales · {s["rising"]:,} rising · '
            f'{s["falling"]:,} falling · {s["n_30"]:,} sales in the last 30 days.</p>'
            f'<div class="move-grid">{"".join(blocks)}</div>')
