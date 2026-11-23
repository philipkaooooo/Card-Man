"""The sales behind a card page: one plain sentence (last sale, trend when it is real) and a table of every
sale, every month and every lowest-listed price. Served from /card/<id>/history.html; no chart is drawn."""
from __future__ import annotations

import html
from datetime import date

from . import charts


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _f(v, cur):
    return "—" if v is None else charts.fmt(float(v), cur)


def _day(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}, {d.year}"


def chips(s: dict, cur: str) -> str:
    """One plain sentence for the shown version and condition: last sale and the trend when it is real."""
    from .history import plain_trend
    last = s["summary"].get("last")
    bits = []
    if last:
        bits.append(f'Last sold {_day(last["t"])} for {_f(last["p"], cur)}')
    t = plain_trend(s["summary"])
    if t:
        bits.append(t)
    return f'<div data-hchips><p class="hline">{e(" · ".join(bits))}</p></div>' if bits else '<div data-hchips></div>'


def table(s: dict, cur: str) -> str:
    rows = "".join(f'<tr><td>{e(date.fromisoformat(b["start"]).strftime("%b %Y"))}</td>'
                   f'<td class="n">{b["n"]}</td><td class="n">{_f(b["median"], cur)}</td>'
                   f'<td class="n">{_f(b["low"], cur)} – {_f(b["high"], cur)}</td></tr>' for b in reversed(s["month"]))
    sales = "".join(f'<tr><td>{e(_day(d["t"]))}</td><td>{e(d["site"])}</td><td>{e(d["cond"].replace("raw", "Ungraded"))}</td>'
                    f'<td class="n">{_f(d["p"], cur)}</td></tr>' for d in reversed(s["sales"]))
    asks = "".join(f'<tr><td>{e(_day(a["d"]))}</td><td class="n">{_f(a["low"], cur)}</td><td class="n">{a["n"]}</td></tr>'
                   for a in reversed(s["asks"][-30:]))
    return ('<details class="htable"><summary>See the numbers</summary>'
            + (f'<h4>By month</h4><div class="wrap"><table class="compact"><tr><th>Month</th><th class="n">Sales</th><th class="n">Typical price</th>'
               f'<th class="n">Price range</th></tr>{rows}</table></div>' if rows else "")
            + (f'<h4>Every sale</h4><div class="wrap"><table class="compact"><tr><th>Date</th><th>Where</th><th>Condition</th>'
               f'<th class="n">Price</th></tr>{sales}</table></div>' if sales else '<p class="muted">No sales of this version seen yet.</p>')
            + (f'<h4>Lowest listed price</h4><div class="wrap"><table class="compact"><tr><th>Day</th><th class="n">Lowest price</th>'
               f'<th class="n">For sale</th></tr>{asks}</table></div>' if asks else "")
            + '</details>')

