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
from .ui_home import Money, e, img, passcode_of, _tables

ON_TAB = ' class="on" aria-current="page"'

MARKET_CSS = """
.mk-games{display:inline-flex;gap:4px;padding:4px;margin:0 0 12px;background:var(--soft);border-radius:999px}
.mk-games a{padding:7px 16px;border-radius:999px;text-decoration:none;font-weight:700;font-size:14px;color:var(--fg2)}
.mk-games a:hover{color:var(--fg)}
.mk-games a.on{background:var(--surface);color:var(--fg);box-shadow:var(--shadow)}
.mhead{display:grid;grid-template-columns:minmax(0,1.7fr) 128px 128px 172px 100px;gap:14px;padding:0 16px 6px;
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
.mrow{display:grid;grid-template-columns:minmax(0,1.7fr) 128px 128px 172px 100px;gap:14px;align-items:center;
background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:14px;padding:10px 16px 10px 13px;
transition:box-shadow .2s,border-color .2s}
.mrow:hover{box-shadow:var(--shadow)}
.mrow.up{border-left-color:var(--teal)}.mrow.down{border-left-color:#c4553b}
.mmain{min-width:0;display:flex;gap:12px;align-items:center}
.mthumb{flex:none;width:52px;height:73px;border-radius:7px;overflow:hidden;background:var(--soft);display:block;
border:1px solid var(--line);box-shadow:0 2px 6px rgba(15,30,45,.14)}
.mthumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s ease}
.mrow:hover .mthumb img{transform:scale(1.06)}
.mthumb.ph{background:repeating-linear-gradient(135deg,var(--soft) 0 6px,var(--surface) 6px 12px);border:0;box-shadow:none}
.mid{min-width:0}
.mname{font-weight:700;font-size:16.5px;color:var(--fg);text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.mname:hover{color:var(--teal-deep)}
.mmeta{display:flex;gap:6px;align-items:center;min-width:0;margin-top:3px}
.mcode{min-width:0;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mgrade{flex:none;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.03em;
background:color-mix(in srgb,var(--teal) 13%,transparent);color:var(--teal-deep)}
.mgrade.raw{background:var(--soft);color:var(--fg2);font-weight:600}
.mfair,.mturn,.mask,.mtick{min-width:0}
.mfair b,.mturn b,.mask b{font-size:17px;font-weight:800;font-variant-numeric:tabular-nums;display:block;color:var(--fg)}
.mturn b span{font-size:11px;font-weight:600;color:var(--muted)}
.mfair small,.mturn small,.mask small{display:block;font-size:11.5px;color:var(--muted);margin-top:3px;
font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.edge{display:inline-block;font-size:11.5px;font-weight:700;margin-left:6px;vertical-align:2px}
.edge.under{color:var(--teal-deep)}.edge.over{color:#b3261e}.edge.at{color:var(--muted)}
.mask .none{font-size:13px;color:var(--muted)}
.mtick .mchip{display:inline-block;font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px;
font-variant-numeric:tabular-nums;white-space:nowrap}
.mchip.up{background:color-mix(in srgb,var(--teal) 14%,transparent);color:var(--teal-deep)}
.mchip.down{background:color-mix(in srgb,#c4553b 14%,transparent);color:#a2452f}
.mchip.hold,.mchip.none{background:var(--soft);color:var(--fg2)}
small.cl{display:none}
@media(max-width:900px){.mrow{grid-template-columns:1fr 1fr;gap:10px 16px}
.mmain{grid-column:1/-1}
small.cl{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:2px;
font-weight:600}}
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


def _days_since(iso, now: datetime) -> int | None:
    try:
        d = date.fromisoformat(str(iso)[:10])
        return max(0, (now.date() - d).days)
    except (ValueError, TypeError):
        return None


def view_market(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo",
                cards=None, pkm=None) -> str:
    M = Money(st, home, cur, now)
    if "price_trend" not in _tables(st):
        return ('<h1>Market</h1><p class="empty">The market board fills once cards start selling. The engine writes it at '
                'the end of every pass, or run <code>python -m cardman.trends refresh</code>.</p>')
    label = qs.get("label", ["all"])[0]
    label = label if label in ("all", "rising", "falling", "steady", "none") else "all"
    sort = qs.get("sort", ["move"])[0]
    sort = sort if sort in ("move", "edge", "sales", "spread", "recent") else "move"
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
            f'<p class="mk-sub">One row per exact printing and grade. <b>Fair</b> is the middle of this version\'s real '
            f'sales. <b>Ask</b> is the cheapest live copy. The distance between them is the edge you get today.</p>'
            + pulse)
    chips = _chips({**qs, "sort": sort, "min": str(min_sales)}, {
        "label": [("all", "Everything"), ("rising", "Climbing"), ("falling", "Slipping"), ("steady", "Holding"),
                  ("none", "Too new to call")],
        "min": [("0", "Any history"), ("3", "3+ sales"), ("6", "6+ sales")],
        "sort": [("move", "Biggest move"), ("edge", "Below fair"), ("sales", "Bestsellers"), ("recent", "Newest sale")],
    })
    if not rows:
        return (head + chips + '<p class="empty">Nothing matches those filters yet. A version shows up once real sales of '
                'that exact printing and grade are stored.</p>')
    cols = ('<div class="mhead">'
            '<span>Card</span>'
            '<span title="Middle sold price of this exact version, in your currency. Uses the last 30 days when 3+ sales '
            'sit there, otherwise all stored sales. The line under it is the middle half (p25-p75).">Fair now</span>'
            '<span title="Copies sold per month. The line under it: newest sale and how many sales back the number.">'
            'Sales pace</span>'
            '<span title="Cheapest live listing in the newest snapshot (up to 3 days old) and its gap to fair. Under fair '
            'means the copy is cheaper than what copies actually sell for. Supply: listed count divided by the monthly '
            'sales pace.">Cheapest ask</span>'
            '<span title="90-day change when 3+ sales sit at each end of the window; otherwise the last week; otherwise '
            'steady or too new.">Trend</span>'
            '</div>')
    name_of = _name_of(cards, pkm)
    body = []
    for r in rows:
        pid, gkey = r["printing_id"], r["gkey"] or "|"
        if pid.startswith("pk:"):
            key = "pkm-" + pid.split(":")[2] if len(pid.split(":")) > 2 else None
        else:
            key = passcode_of(pid)
        nm = name_of(pid) or r["label"].split(" · ")[0]
        nm_html = (f'<a class="mname" href="{e(card_href(key))}">{e(nm)}</a>' if key
                   else f'<span class="mname">{e(nm)}</span>')
        thumb = (f'<a class="mthumb" href="{e(card_href(key))}" tabindex="-1" aria-hidden="true">{img(key, "small", nm, "")}</a>'
                 if key else '<span class="mthumb ph" aria-hidden="true"></span>')
        grade = _grade(gkey)
        pill = f'<span class="mgrade{" raw" if not grade else ""}">{e(grade or "Raw")}</span>'
        lab = r["trend_label"] if r["trend_label"] in ("rising", "falling") else r["move_label"]
        cls = "up" if lab == "rising" else "down" if lab == "falling" else "hold" if lab == "steady" else "none"
        why = e(r["trend_why"] or r["change_why"] or
                ("the middle of the last 7 days of sales against the 7 before it" if r.get("short_move") is not None
                 else "not enough dated sales for a trend yet"))
        if r["change_90"] is not None:
            pct, span = r["change_90"], "3mo"
        elif r["short_move"] is not None:
            pct, span = r["short_move"], "1wk"
        elif r["change_span"] is not None:
            pct, span = r["change_span"], "total"
        else:
            pct, span = None, ""
        if pct is not None and abs(pct) >= 1:
            chip = f"{pct:+.0f}% {span}"
            if span == "total":
                cls = "up" if pct > 0 else "down"
                why = e("the middle of the newest three sales against the middle of the earliest three")
        elif pct is not None or lab == "steady" or (r["plain"] or "").startswith("Steady"):
            chip = "flat"
        else:
            chip = "too soon" if r["n_sales"] >= 6 else "thin"
        if chip == "flat":
            cls = "hold"
        use30 = (r["n_30"] or 0) >= 3
        fair = r["median_30"] if use30 else r["median"]
        typ_lo = r["p25"] if r["p25"] is not None else r["low"]
        typ_hi = r["p75"] if r["p75"] is not None else r["high"]
        n = r["n_sales"]
        rng = f"{M(typ_lo)}–{M(typ_hi)}" if typ_lo != typ_hi else M(typ_lo)
        last = _days_since(r["last_sale"], now)
        last_txt = "today" if last == 0 else f"{last}d" if last is not None else "—"
        pace = r["sales_month"]
        pace_txt = (f"{pace:.1f}" if pace < 10 else f"{pace:.0f}") if pace else "—"
        supd = (r["ask_n"] / (pace / 30.44)) if r["ask_n"] and pace else None
        sup_txt = ("<1d" if supd < 1 else f"≈{supd:.0f}d") if supd is not None else None
        askv = r["ask_low"] if r["ask_low"] is not None else r["ask_median"]
        if askv:
            edge = (fair - askv) / fair * 100 if fair else None
            ecls = ("under" if edge >= 2 else "over" if edge <= -2 else "at") if edge is not None else "at"
            etxt = (f"{edge:.0f}% under fair" if ecls == "under" else f"{abs(edge):.0f}% above fair" if ecls == "over"
                    else "at fair")
            asub = f'{r["ask_n"]} listed {sup_txt}' if r["ask_n"] and sup_txt else (
                f'{r["ask_n"]} listed' if r["ask_n"] else _short_day(r["ask_day"]))
            atitle = (f'Cheapest of {r["ask_n"]} live listings in the {_short_day(r["ask_day"])} snapshot; '
                      f'{"under a day" if supd < 1 else f"about {supd:.0f} days"} of supply at the current sales pace'
                      if supd is not None else
                      f'Cheapest live listing in the {_short_day(r["ask_day"])} snapshot')
            ask_html = (f'<b title="{e(atitle)}">{M(askv)}<span class="edge {ecls}">{e(etxt)}</span></b>'
                        f'<small>{e(asub)}</small>')
        else:
            ask_html = '<span class="none">no live ask</span>'
        body.append(
            f'<li class="mrow {cls}"><div class="mmain">{thumb}'
            f'<span class="mid">{nm_html}<span class="mmeta"><span class="mcode">{e(r["label"])}</span>{pill}</span></span></div>'
            f'<div class="mfair"><small class="cl">Fair now</small>'
            f'<b title="Middle of the {"last 30 days of" if use30 else ""} sales of this exact version: {n} sold between '
            f'{M(r["low"])} and {M(r["high"])}">{M(fair)}</b>'
            f'<small title="Middle half of all sales of this version">{n} sold · {rng}</small></div>'
            f'<div class="mturn"><small class="cl">Sales pace</small>'
            f'<b title="Copies sold per month across {n} stored sales">{pace_txt}<span>/mo</span></b>'
            f'<small>last {last_txt} · n={n}</small></div>'
            f'<div class="mask"><small class="cl">Cheapest ask</small>{ask_html}</div>'
            f'<div class="mtick"><small class="cl">Trend</small>'
            f'<span class="mchip {cls}" title="{why}">{chip}</span></div></li>')
    shown = (f'<p class="muted small">Showing {len(rows)} of {s["series"]:,} versions'
             + (", the top 200 for this sort" if len(rows) >= 200 else "") + '.</p>')
    note = ('<p class="muted small">Prices in your display currency, each sale converted at its own date. Fair is sold '
            'evidence only; asks come from the newest nightly snapshot and never move it.</p>')
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
