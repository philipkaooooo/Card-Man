"""Market board: the whole catalog's price history, one line per printing and grade.

Nothing here is an estimate of its own: every median, trend and change comes from the same sold evidence the
card pages use, already deduped and condition-checked. Live asks are shown beside the sold price as a separate
series, never mixed in. A printing with no real sales does not appear.
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlencode

from . import trends
from .games import card_href
from .ui_home import Money, e, passcode_of, _tables

ON_TAB = ' class="on" aria-current="page"'

MARKET_CSS = """
.mk-games{display:inline-flex;gap:4px;padding:4px;margin:0 0 12px;background:var(--soft);border-radius:999px}
.mk-games a{padding:7px 16px;border-radius:999px;text-decoration:none;font-weight:700;font-size:14px;color:var(--fg2)}
.mk-games a:hover{color:var(--fg)}
.mk-games a.on{background:var(--surface);color:var(--fg);box-shadow:var(--shadow)}
.mhead{display:grid;grid-template-columns:minmax(0,1.6fr) 150px minmax(0,1fr) 230px;gap:18px;padding:0 16px 6px;
font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.mhead span{cursor:help;border-bottom:1px dotted color-mix(in srgb,var(--muted) 45%,transparent);justify-self:start}
@media(max-width:900px){.mhead{display:none}}
.move-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:6px 0 14px}
.move-grid h3{margin:0 0 4px;font-size:13px;color:var(--fg2);text-transform:uppercase;letter-spacing:.04em}
ul.mini{list-style:none;margin:0;padding:0}ul.mini li{padding:3px 0;font-size:13px;border-bottom:1px solid var(--line)}
ul.mini li:last-child{border-bottom:0}.up{color:var(--teal-deep);font-weight:600}.down{color:#b3261e;font-weight:600}
@media(max-width:760px){.move-grid{grid-template-columns:1fr}}
.mk-sub{max-width:72ch;font-size:15px;color:var(--fg2);margin:0 0 12px}
.mk-pulse{display:flex;height:14px;border-radius:999px;overflow:hidden;background:var(--soft);margin:2px 0 8px;max-width:720px}
.mk-pulse span{display:block;height:100%;min-width:2px}
.p-up{background:var(--teal)}.p-down{background:#c4553b}.p-hold{background:#c9c2b4}.p-none{background:#e6e0d4}
.mk-pulse-k{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;color:var(--fg2);margin:0 0 16px;align-items:center}
.mk-pulse-k i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
.mk-pulse-k i.p-up,.mk-pulse-k i.p-down,.mk-pulse-k i.p-hold,.mk-pulse-k i.p-none{display:inline-block}
.mkstat{margin-left:auto;color:var(--muted)}.mkstat b{color:var(--fg);font-variant-numeric:tabular-nums}
.mlist{list-style:none;margin:0 0 10px;padding:0;display:grid;gap:10px}
.mrow{display:grid;grid-template-columns:minmax(0,1.6fr) 150px minmax(0,1fr) 230px;gap:18px;align-items:center;
background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 16px;transition:box-shadow .2s}
.mrow:hover{box-shadow:var(--shadow)}
.mmain{min-width:0}
.mname{font-weight:700;font-size:17px;color:var(--fg);text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.mname:hover{color:var(--teal-deep)}
.mcode{display:block;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.msell b{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums;display:block;color:var(--fg)}
.msell small{font-size:10.5px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.mtick .mchip{display:inline-block;font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px}
.mchip.up{background:color-mix(in srgb,var(--teal) 14%,transparent);color:var(--teal-deep)}
.mchip.down{background:color-mix(in srgb,#c4553b 14%,transparent);color:#a2452f}
.mchip.hold,.mchip.none{background:var(--soft);color:var(--fg2)}
.mtick small{display:block;font-size:12px;color:var(--muted);margin-top:5px}
.mask{min-width:0}
.abars{display:grid;gap:4px}
.abar{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:8px;align-items:center;font-size:12px;color:var(--fg2)}
.abar i{height:8px;border-radius:4px;background:var(--soft);position:relative;display:block}
.abar i span{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.abar em{font-style:normal;font-variant-numeric:tabular-nums}
.abar.sold i span{background:var(--fg2);opacity:.5}
.abar.ask i span{background:var(--teal)}
.abar.ask.over i span{background:#c4553b}
.ask-gap{font-size:12px;color:var(--muted);margin-top:4px}
@media(max-width:900px){.mrow{grid-template-columns:minmax(0,1fr) 150px;gap:8px 16px}
.mtick{grid-column:1/-1}.mask{grid-column:1/-1}}
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


def _name_of(cards, pkm):
    def name(pid: str) -> str:
        try:
            if pid.startswith("pk:"):
                return (pkm.card_name(pid.split(":")[2]) or "") if pkm is not None else ""
            pc = passcode_of(pid)
            if pc and cards is not None:
                c = cards.card(pc) or {}
                return c.get("name_en") or c.get("name_ja") or ""
        except Exception:
            pass
        return ""
    return name


def _short_day(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return f"{d:%b} {d.day}"
    except (ValueError, TypeError):
        return "—"


def view_market(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo",
                cards=None, pkm=None) -> str:
    M = Money(st, home, cur, now)
    if "price_trend" not in _tables(st):
        return ('<h1>Market</h1><p class="empty">The market board fills once cards start selling. The engine writes it at '
                'the end of every pass, or run <code>python -m cardman.trends refresh</code>.</p>')
    label = qs.get("label", ["all"])[0]
    label = label if label in ("all", "rising", "falling", "steady", "none") else "all"
    sort = qs.get("sort", ["move"])[0]
    sort = sort if sort in ("move", "sales", "spread", "recent") else "move"
    min_sales = qs.get("min", ["0"])[0]
    min_sales = int(min_sales) if min_sales.isdigit() else 0
    s = trends.summary(st, game)
    rows = trends.board(st, game=game, label=None if label == "all" else label, min_sales=min_sales, sort=sort, limit=200)
    name = {"ygo": "Yu-Gi-Oh!", "pkm": "Pokémon"}.get(game, game)
    total = s["series"] or 1

    def pct(n):
        return max(0.0, n / total * 100)

    pulse = ""
    if s["series"]:
        segs = (("p-up", s["rising"]), ("p-down", s["falling"]), ("p-hold", s["steady"]), ("p-none", s["no_trend"]))
        pulse = ('<div class="mk-pulse" role="img" aria-label="'
                 f'{s["rising"]} climbing, {s["falling"]} slipping, {s["steady"]} holding, {s["no_trend"]} too new to call">'
                 + "".join(f'<span class="{c}" style="width:{pct(n):.1f}%"></span>' for c, n in segs if n) + '</div>'
                 '<div class="mk-pulse-k">'
                 f'<span><i class="p-up"></i>{s["rising"]:,} climbing</span>'
                 f'<span><i class="p-down"></i>{s["falling"]:,} slipping</span>'
                 f'<span><i class="p-hold"></i>{s["steady"]:,} holding</span>'
                 f'<span><i class="p-none"></i>{s["no_trend"]:,} too new to call</span>'
                 f'<span class="mkstat"><b>{s["with_asks"]:,}</b> listed now · <b>{s["n_30"]:,}</b> sales in the last 30 days</span></div>')
    tabs = ('<div class="mk-games" role="tablist" aria-label="Game">'
            + "".join(f'<a role="tab"{ON_TAB if g == game else ""} href="/game?{e(urlencode({"g": g, "back": "/market"}))}">'
                      f'{e(lab)}</a>' for g, lab in (("ygo", "Yu-Gi-Oh!"), ("pkm", "Pokémon")))
            + '</div>')
    head = (f'<h1>The {e(name)} market</h1>' + tabs +
            f'<p class="mk-sub">Every card here has sold at least once. The big number is what copies <b>sold for</b> — the '
            f'middle of the real sales, not a seller\'s hope. Ask bars are the cheapest live listing, kept separate.</p>'
            + pulse)
    chips = _chips({**qs, "sort": sort, "min": str(min_sales)}, {
        "label": [("all", "Everything"), ("rising", "Climbing"), ("falling", "Slipping"), ("steady", "Holding"),
                  ("none", "Too new to call")],
        "min": [("0", "Any history"), ("3", "3+ sales"), ("6", "6+ sales")],
        "sort": [("move", "Biggest move"), ("sales", "Bestsellers"), ("spread", "Widest ask gap"), ("recent", "Newest sale")],
    })
    if not rows:
        return (head + chips + '<p class="empty">Nothing matches those filters yet. A version shows up once real sales of '
                'that exact printing and grade are stored.</p>')
    cols = ('<div class="mhead">'
            '<span>Card</span>'
            '<span title="The middle of this version\'s real sales: half the copies sold for less, half for more.">Sells for</span>'
            '<span title="Which way recent sales moved, and how many sales back the call.">Moved</span>'
            '<span title="Cheapest live listing against the sold price. A gap of three times or more usually means a '
            'different variant is on sale - open the card before believing it.">Sold vs asking</span>'
            '</div>')
    name_of = _name_of(cards, pkm)
    body = []
    for r in rows:
        pid, gkey = r["printing_id"], r["gkey"] or "|"
        pc = passcode_of(pid)
        nm = name_of(pid) or r["label"].split(" · ")[0]
        nm_html = (f'<a class="mname" href="{e(card_href(pc))}">{e(nm)}</a>' if pc
                   else f'<span class="mname">{e(nm)}</span>')
        grade = _grade(gkey)
        code = " · ".join(x for x in (r["label"], grade) if x)
        lab = r["trend_label"] or r["move_label"]
        cls = "up" if lab == "rising" else "down" if lab == "falling" else "hold" if lab == "steady" else "none"
        chip = r["plain"] or "Too new to call"
        ch90 = (f' <span class="muted small">{r["change_90"]:+.0f}% in 3 months</span>'
                if r["change_90"] is not None else "")
        why = e(r["trend_why"] or r["change_why"] or
                ("the middle of the last 7 days of sales against the 7 before it" if r.get("short_move") is not None
                 else "not enough dated sales for a trend yet"))
        sold = r["median"]
        askv = r["ask_low"] if r["ask_low"] is not None else r["ask_median"]
        if askv:
            soldv = sold or 0
            gap = (askv / soldv - 1) * 100 if soldv else None
            capped = bool(soldv) and askv > soldv * 3
            top = max(soldv, soldv * 3 if capped else askv) or 1
            over = askv > soldv
            gaptxt = ("Asking far above the sold price — three times or more" if capped else
                      "Asking in line with the sold price" if gap is not None and abs(gap) < 2 else
                      f"Asking {abs(gap):.0f}% above the sold price" if over and gap is not None else
                      f"Asking {abs(gap):.0f}% below the sold price" if gap is not None else
                      "Asking price against the sold price")
            if r["ask_n"] and r["ask_day"]:
                gaptxt += f' · {r["ask_n"]} listed {_short_day(r["ask_day"])}'
            bars = (f'<div class="abars">'
                    f'<div class="abar sold"><em>Sold</em><i><span style="width:{soldv / top * 100:.0f}%"></span></i>'
                    f'<em>{M(sold)}</em></div>'
                    f'<div class="abar ask{" over" if over else ""}"><em>Asking</em>'
                    f'<i><span style="width:{min(askv / top * 100, 100):.0f}%"></span></i><em>{M(askv)}</em></div></div>'
                    f'<div class="ask-gap">{e(gaptxt)}</div>')
        else:
            bars = '<div class="ask-gap">No copy is listed right now, so the sold price stands alone.</div>'
        body.append(
            f'<li class="mrow"><div class="mmain">{nm_html}<span class="mcode">{e(code)}</span></div>'
            f'<div class="msell"><b title="Middle of this version\'s real sales: {r["n_sales"]} sold between '
            f'{M(r["low"])} and {M(r["high"])}">{M(sold)}</b><small>Sells for</small></div>'
            f'<div class="mtick"><span class="mchip {cls}" title="{why}">{e(chip)}</span>{ch90}'
            f'<small>{r["n_sales"]} sale{"s" if r["n_sales"] != 1 else ""} · last sold {_short_day(r["last_sale"])}'
            + (f' · {r["n_30"]} in the last 30 days' if r["n_30"] else "") + '</small></div>'
            f'<div class="mask">{bars}</div></li>')
    shown = (f'<p class="muted small">Showing {len(rows)} of {s["series"]:,} versions'
             + (", the top 200 for this sort" if len(rows) >= 200 else "") + '.</p>')
    note = ('<p class="muted small">Prices in your display currency, each sale converted at its own date. The ask bar is '
            'the cheapest copy in the newest nightly snapshot and never moves the sold number.</p>')
    return head + chips + cols + f'<ul class="mlist">{"".join(body)}</ul>' + shown + note


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
