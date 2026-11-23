"""Market board: the whole catalog's price history, one line per printing and grade.

Nothing here is an estimate of its own: every median, trend and change comes from the same sold evidence the
card pages use, already deduped and condition-checked. Live asks are shown beside the sold price as a separate
series, never mixed in. A printing with no real sales does not appear.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from . import pc, trends
from .games import card_href
from .ui_home import Money, e, img, passcode_of, _tables

ON_TAB = ' class="on" aria-current="page"'

MARKET_CSS = """
.mk-games{display:inline-flex;gap:4px;padding:4px;margin:0 0 12px;background:var(--soft);border-radius:999px}
.mk-games a{padding:7px 16px;border-radius:999px;text-decoration:none;font-weight:700;font-size:14px;color:var(--fg2)}
.mk-games a:hover{color:var(--fg)}
.mk-games a.on{background:var(--surface);color:var(--fg);box-shadow:var(--shadow)}
.mhead{display:grid;grid-template-columns:minmax(0,1.55fr) 118px 118px 160px 124px 100px;gap:12px;padding:0 16px 6px;
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
.mk-best{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;margin:0 0 12px;padding:8px 14px;
background:color-mix(in srgb,var(--teal) 8%,var(--surface));border:1px solid color-mix(in srgb,var(--teal) 25%,var(--line));
border-radius:12px}
.mk-best .bb-h{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--teal-deep);font-weight:700}
.mk-best .bb{font-size:13.5px;color:var(--fg);text-decoration:none;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;max-width:min(340px,100%)}
.mk-best .bb b{font-variant-numeric:tabular-nums}
.mk-best .bb i{font-style:normal;color:var(--teal-deep);font-weight:700}
.mk-best .bb:hover{color:var(--teal-deep)}
.mlist{list-style:none;margin:0 0 10px;padding:0;display:grid;gap:10px}
.mrow{display:grid;grid-template-columns:minmax(0,1.55fr) 118px 118px 160px 124px 100px;gap:12px;align-items:center;
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
.mfair,.mturn,.mask,.mtick,.mpcell{min-width:0}
.mfair b,.mturn b,.mask b{font-size:17px;font-weight:800;font-variant-numeric:tabular-nums;display:block;color:var(--fg)}
.mturn b span{font-size:11px;font-weight:600;color:var(--muted)}
.mfair small,.mturn small,.mask small{display:block;font-size:11.5px;color:var(--muted);margin-top:3px;
font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.edge{display:inline-block;font-size:11.5px;font-weight:700;margin-left:6px;vertical-align:2px}
.edge.under{color:var(--teal-deep)}.edge.over{color:#b3261e}.edge.at{color:var(--muted)}
.mask .none{font-size:13px;color:var(--muted)}
.mask b a{color:inherit;text-decoration:none;border-bottom:1px dashed color-mix(in srgb,var(--fg2) 45%,transparent)}
.mask b a:hover{color:var(--teal-deep);border-color:var(--teal-deep)}
.mtick .mchip{display:inline-block;font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px;
font-variant-numeric:tabular-nums;white-space:nowrap}
.mchip.up{background:color-mix(in srgb,var(--teal) 14%,transparent);color:var(--teal-deep)}
.mchip.down{background:color-mix(in srgb,#c4553b 14%,transparent);color:#a2452f}
.mchip.hold,.mchip.none{background:var(--soft);color:var(--fg2)}
.mpc{min-width:0;display:block}.mpc svg{display:block;width:116px;height:34px;overflow:visible}
a.mpcl{text-decoration:none;color:inherit;border-radius:6px}
a.mpcl:hover small,a.mpcl:focus-visible small{color:var(--teal-deep)}
.mpc .pline{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mpc.up .pline{stroke:var(--teal)}.mpc.down .pline{stroke:#c4553b}
.mpc .pdot{stroke:none}.mpc.up .pdot{fill:var(--teal)}.mpc.down .pdot{fill:#c4553b}
.mpc small{display:block;font-size:11.5px;color:var(--muted);margin-top:2px;font-variant-numeric:tabular-nums;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mpc .pcnone{font-size:13px;color:var(--muted)}
.flip{background:color-mix(in srgb,#b98a1e 7%,var(--surface));border-color:color-mix(in srgb,#b98a1e 28%,var(--line));
flex-direction:column;align-items:stretch;gap:3px}
.flip .bb-h{color:#8a6614}
.flip .bb-h small{font-size:11.5px;letter-spacing:0;text-transform:none;font-weight:600;color:color-mix(in srgb,#8a6614 70%,var(--fg2))}
.flip .bb{max-width:100%;white-space:normal;display:block;padding:2px 0;font-size:13.5px}
.flip .bb b{font-weight:700}.flip .bb i{color:#8a6614;font-style:normal;font-weight:700}
.flip .bb:hover i,.flip a.bb:hover b{color:#6d5110}
.tag{display:inline-block;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;padding:2px 6px;
border-radius:4px;margin-right:7px;vertical-align:1px}
.tag.now{background:var(--teal);color:#fff}.tag.wait{background:color-mix(in srgb,#8a6614 16%,var(--surface));color:#8a6614}
small.cl{display:none}
@media(max-width:900px){.mrow{grid-template-columns:1fr 1fr;gap:10px 16px}
.mmain{grid-column:1/-1}
small.cl{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:2px;
font-weight:600}}
"""


def _grade(gkey: str) -> str:
    gc, _, gr = (gkey or "|").partition("|")
    return f"{gc} {gr}".strip() if gc else ""


def _spark(points: list, label: str, tip: str, href: str | None = None) -> str:
    """One year of PriceCharting weekly points, drawn small. Colour is the direction of the year, the dot is today."""
    vs = [v for _t, v in points]
    if len(vs) < 2:
        return ""
    lo, hi = min(vs), max(vs)
    span = hi - lo or 1.0
    w, h, pad = 116, 34, 3
    step = (w - 2 * pad) / (len(vs) - 1)
    xy = " ".join(f"{pad + i * step:.1f},{h - pad - (h - 2 * pad) * ((v - lo) / span):.1f}" for i, v in enumerate(vs))
    y = h - pad - (h - 2 * pad) * ((vs[-1] - lo) / span)
    cls = "up" if vs[-1] >= vs[0] else "down"
    tag = (f'<a class="mpc mpcl {cls}" href="{e(href)}" target="_blank" rel="noreferrer noopener" '
           f'data-tip="{e(tip)}" title="Open this card on PriceCharting">' if href else
           f'<span class="mpc {cls}" data-tip="{e(tip)}">')
    close = "</a>" if href else "</span>"
    return (tag + f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{e(label)}">'
            f'<polyline class="pline" points="{xy}"/>'
            f'<circle class="pdot" cx="{w - pad:.1f}" cy="{y:.1f}" r="2.6"/></svg>'
            f'<small>{e(label)}</small>' + close)


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


def _card_key(pid: str):
    if pid.startswith("pk:"):
        parts = pid.split(":")
        return "pkm-" + parts[2] if len(parts) > 2 else None
    return passcode_of(pid)


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


def _ask_links(st, now: datetime, home: str, pids: list[str]) -> dict[tuple[str, str], str]:
    """The cheapest live listing behind each ask, same evidence rules as the nightly snapshot: engine 95% on the
    card, 90% on the printing, no lots, no ended listings, nothing unseen for three days."""
    from decimal import Decimal

    from .fx import FxTable
    from .history import ASK_CARD_PROB, ASK_PRINTING_PROB
    from .listing_parser import _GRADE_RE
    if not pids or not {"card_listing", "listing"} <= _tables(st):
        return {}
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=3))
    except Exception:
        fx = None
    fresh = (now - timedelta(days=3)).isoformat()
    marks = ",".join("?" * len(pids))
    rows = st.db.execute(
        "SELECT c.printing_id, l.price, l.currency, l.data_json, l.title, l.url FROM card_listing c "
        f"JOIN listing l ON l.key=c.key WHERE c.printing_id IN ({marks}) AND c.card_prob>=? AND c.printing_prob>=? "
        "AND c.lot=0 AND l.kind='ask' AND c.status<>'REJECTED' AND l.ended_at IS NULL AND l.last_seen>=?",
        (*pids, ASK_CARD_PROB, ASK_PRINTING_PROB, fresh))
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for pid, price, cur, dj, title, url in rows:
        if not url:
            continue
        try:
            v = float(price) if cur == home else (float(fx.convert(Decimal(str(price)), cur, home, now)) if fx else None)
        except Exception:
            continue
        if v is None:
            continue
        data = json.loads(dj or "{}")
        gc, gr = data.get("grade_company"), data.get("grade")
        if not gc:
            m = _GRADE_RE.search(title or "")
            if m:
                gc, gr = m.group(1).upper().replace("BECKETT", "BGS"), m.group(2)
            elif "grad" in (data.get("condition") or "").lower():
                continue
        key = (pid, f"{gc or ''}|{gr or ''}")
        if key not in best or v < best[key][0]:
            best[key] = (v, url)
    return {k: url for k, (_v, url) in best.items()}


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
            f'<p class="mk-sub">One row per exact printing and grade. <b>Typical</b> is the middle of what this version '
            f'actually sold for. <b>Ask</b> is the cheapest copy on sale now - click it to open the listing.</p>'
            + pulse)
    chips = _chips({**qs, "sort": sort, "min": str(min_sales)}, {
        "label": [("all", "Everything"), ("rising", "Climbing"), ("falling", "Slipping"), ("steady", "Holding"),
                  ("none", "Too new to call")],
        "min": [("0", "Any history"), ("3", "3+ sales"), ("6", "6+ sales")],
        "sort": [("move", "Biggest move"), ("edge", "Below typical"), ("sales", "Bestsellers"), ("recent", "Newest sale")],
    })
    cols = ('<div class="mhead">'
            '<span>Card</span>'
            '<span title="Median of this exact version\'s sales, in your currency. Uses the last 30 days when 3+ sales '
            'sit there, otherwise all stored sales. The line under it is the middle half (p25-p75).">Typical sold</span>'
            '<span title="Copies sold per month. The line under it: newest sale and how many sales back the number.">'
            'Sales pace</span>'
            '<span title="Cheapest live listing in the newest snapshot (up to 3 days old), the listing itself one click '
            'away, and its gap to the typical price. Supply: listed count divided by the monthly sales pace.">'
            'Cheapest ask</span>'
            '<span title="PriceCharting\'s own weekly sold prices for this grade (Ungraded for raw rows), last 12 '
            'months. The line under it is the year change. A verdict from a second market, not a copy of ours.">'
            '1 yr · PC</span>'
            '<span title="90-day change when 3+ sales sit at each end of the window; otherwise the last week; otherwise '
            'newest three sales against earliest three; otherwise steady or too new.">Trend</span>'
            '</div>')
    name_of = _name_of(cards, pkm)

    def fair_of(r):
        return r["median_30"] if (r["n_30"] or 0) >= 3 else r["median"]

    deals = []
    for d in trends.board(st, game=game, sort="edge", min_sales=3, limit=80):
        f = fair_of(d)
        if d["ask_low"] and f and (f - d["ask_low"]) / f >= 0.02:
            deals.append(d)
            if len(deals) >= 3:
                break
    links = _ask_links(st, now, home, sorted({r["printing_id"] for r in rows} | {d["printing_id"] for d in deals}))
    pcrows = pc.rows_for(st, [r["printing_id"] for r in rows])

    def usd_home(usd: float | None):
        if usd is None:
            return None
        try:
            if M.fx is not None:
                return float(M.fx.convert(Decimal(str(usd)), "USD", M.home, now))
        except Exception:
            return None
        return None

    def pc_money(usd: float) -> str:
        v = usd_home(usd)
        return M(v) if v is not None else f"${usd:,.2f}"

    def vol_per_month(vol):
        m = re.match(r"^(\d+)\s+sales?\s+per\s+(day|week|month|year)$", (vol or "").strip())
        if not m:
            return None
        per = {"day": 30.0, "week": 4.35, "month": 1.0, "year": 1 / 12}[m.group(2)]
        return int(m.group(1)) * per

    # Buy raw at a live ask, grade it, sell it as PSA 10: only the money left after the real costs counts.
    from .engine import load_profile
    try:
        profile = load_profile()
    except Exception:
        profile = {}
    route = dict((profile.get("routes") or {}).get("INTL") or {})
    exit_c = dict(profile.get("exit") or {})
    k_buy = 1 + float(route.get("payment_fx_spread_pct") or 0)
    fixed = float(route.get("inbound_shipping") or 0) + float((profile.get("grading") or {}).get("cost_home") or 0)
    fee_pct = (float(exit_c.get("platform_fee_pct") or 0) + float(exit_c.get("returns_reserve_pct") or 0)
               + float(exit_c.get("payout_fx_spread_pct") or 0))
    ship_out = float(exit_c.get("outbound_shipping") or 0)
    target = float(profile.get("target_return") or 0)

    buys, watches = [], []
    for r in rows:
        if (r["gkey"] or "|") != "|":
            continue
        pcr = pcrows.get(r["printing_id"])
        psa = pc.current(pcr, "PSA 10")
        used = pc.current(pcr, "Ungraded")
        f = fair_of(r)
        if not psa or not psa.get("p") or not used or not used.get("p") or not f:
            continue
        pu = usd_home(used["p"])
        if not pu or not (f / 3 <= pu <= f * 3):
            continue                       # PC's own ungraded price is far from ours: not the same printing
        rate = vol_per_month(psa.get("vol"))
        if rate is None or rate < 1:
            continue                       # PSA 10 sells less than once a month: a shelf, not a market
        psa_home = usd_home(psa["p"])
        if not psa_home:
            continue
        net = psa_home * (1 - fee_pct) - ship_out
        if net <= fixed:
            continue                       # even a free raw card cannot pay the grader and the shipping
        ask = r["ask_low"]
        if ask:
            if not (f / 3 <= ask <= f * 3):
                continue                   # the cheapest ask is a different printing than PC's row
            profit = net - k_buy * ask - fixed
            if profit > 0:
                buys.append((profit, r, ask, net, psa))
            continue
        pay = (net - (1 + target) * fixed) / ((1 + target) * k_buy)
        if pay > 0:
            watches.append((net - k_buy * f - fixed, r, pay, net, psa))
    buys.sort(key=lambda x: -x[0])
    watches.sort(key=lambda x: -x[0])
    flip = ""
    picks = [("now",) + b for b in buys[:3]] + [("wait",) + w for w in watches[:3 - min(3, len(buys))]]
    if picks:
        items = []
        for kind, room, r, ask, net, psa in picks:
            pid = r["printing_id"]
            k = _card_key(pid)
            nm = name_of(pid) or r["label"].split(" · ")[0]
            vol = (psa.get("vol") or "").replace(" per ", "/")
            if kind == "now":
                all_in = k_buy * ask + fixed
                tag = '<span class="tag now">buy now</span>'
                line = f'{M(ask)} ask · {M(all_in)} all-in · sells {M(net)} · <i>+{M(room)}</i>'
                tip = (f'Cheapest live raw ask {M(ask)}; with grading and shipping the card costs {M(all_in)}. '
                       f'PSA 10 sells {M(net)} after selling fees. Profit {M(room)}.')
                href = links.get((pid, "|"))
            else:
                tag = '<span class="tag wait">no ask under</span>'
                line = f'{M(ask)} max raw · {M(k_buy * ask + fixed)} all-in · sells {M(net)}'
                tip = (f'No live raw ask clears this card\'s costs right now. Pay at most {M(ask)} for the raw '
                       f'(all-in {M(k_buy * ask + fixed)}) and the PSA 10, at {pc_money(psa["p"])}, nets {M(net)} '
                       f'with your {target * 100:.0f}% target met.')
                href = None
            inner = (f'<b>{e(nm)}</b> <i>{line}</i>')
            if href:
                items.append(f'<a class="bb" href="{e(href)}" target="_blank" rel="noreferrer noopener" '
                             f'title="{e(tip + " PSA 10 trades " + vol + ".")}">{tag}{inner}</a>')
            else:
                items.append(f'<span class="bb" title="{e(tip + " PSA 10 trades " + vol + ".")}">{tag}{inner}</span>')
        flip = ('<div class="mk-best flip"><span class="bb-h">Buy raw → grade → sell PSA 10'
                f'<small> after your costs: {M(fixed)} fixed, {fee_pct * 100:.1f}% selling fees</small></span>'
                + "".join(items) + '</div>')
    best = ""
    if deals:
        items = []
        for d in deals:
            pid = d["printing_id"]
            k = _card_key(pid)
            url = links.get((pid, d["gkey"] or "|"))
            f = fair_of(d)
            pct = (f - d["ask_low"]) / f * 100
            nm = name_of(pid) or d["label"].split(" · ")[0]
            ext = ' target="_blank" rel="noreferrer noopener"' if url else ""
            items.append(f'<a class="bb" href="{e(url or (card_href(k) if k else "/market"))}"{ext}>'
                         f'<b>{M(d["ask_low"])}</b> {e(nm)} <i>{pct:.0f}% under</i></a>')
        best = ('<div class="mk-best"><span class="bb-h">Under typical now</span>' + "".join(items) + '</div>')
    if not rows:
        return (head + chips + best + '<p class="empty">Nothing matches those filters yet. A version shows up once real '
                'sales of that exact printing and grade are stored.</p>')
    body = []
    for r in rows:
        pid, gkey = r["printing_id"], r["gkey"] or "|"
        key = _card_key(pid)
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
            etxt = (f"{edge:.0f}% under typical" if ecls == "under" else
                    f"{abs(edge):.0f}% above typical" if ecls == "over" else "at typical")
            asub = f'{r["ask_n"]} listed {sup_txt}' if r["ask_n"] and sup_txt else (
                f'{r["ask_n"]} listed' if r["ask_n"] else _short_day(r["ask_day"]))
            atitle = (f'Cheapest of {r["ask_n"]} live listings in the {_short_day(r["ask_day"])} snapshot; '
                      f'{"under a day" if supd < 1 else f"about {supd:.0f} days"} of supply at the current sales pace'
                      if supd is not None else
                      f'Cheapest live listing in the {_short_day(r["ask_day"])} snapshot')
            lur = links.get((pid, gkey))
            num = (f'<a href="{e(lur)}" target="_blank" rel="noreferrer noopener" '
                   f'title="Open the listing">{M(askv)}</a>' if lur else M(askv))
            ask_html = (f'<b title="{e(atitle)}">{num}<span class="edge {ecls}">{e(etxt)}</span></b>'
                        f'<small>{e(asub)}</small>')
        else:
            ask_html = '<span class="none">no live ask</span>'
        pcr = pcrows.get(pid)
        gs = pc.grade_series(pcr, gkey)
        if gs:
            gname, gpts = gs
            gchg = (gpts[-1][1] / gpts[0][1] - 1) * 100 if gpts[0][1] else None
            cur_pc = pc.current(pcr, gname) or {}
            vol = (cur_pc.get("vol") or "").replace(" per ", "/")
            tip = (f"PriceCharting {gname}: {pc_money(gpts[-1][1])} now"
                   + (f", {gchg:+.0f}% over the year" if gchg is not None else "")
                   + (f"; {vol} recently" if vol else "") + ". Weekly points, 12 months.")
            pc_html = _spark(gpts, f"{gname} · {gchg:+.0f}%" if gchg is not None else gname, tip, pcr.get("url"))
        else:
            pc_html = '<span class="mpc"><span class="pcnone">—</span><small>not swept</small></span>'
        body.append(
            f'<li class="mrow {cls}"><div class="mmain">{thumb}'
            f'<span class="mid">{nm_html}<span class="mmeta"><span class="mcode">{e(r["label"])}</span>{pill}</span></span></div>'
            f'<div class="mfair"><small class="cl">Typical sold</small>'
            f'<b title="Middle of the {"last 30 days of" if use30 else ""} sales of this exact version: {n} sold between '
            f'{M(r["low"])} and {M(r["high"])}">{M(fair)}</b>'
            f'<small title="Middle half of all sales of this version">{n} sold · {rng}</small></div>'
            f'<div class="mturn"><small class="cl">Sales pace</small>'
            f'<b title="Copies sold per month across {n} stored sales">{pace_txt}<span>/mo</span></b>'
            f'<small>last {last_txt} · n={n}</small></div>'
            f'<div class="mask"><small class="cl">Cheapest ask</small>{ask_html}</div>'
            f'<div class="mpcell"><small class="cl">1 yr · PC</small>{pc_html}</div>'
            f'<div class="mtick"><small class="cl">Trend</small>'
            f'<span class="mchip {cls}" title="{why}">{chip}</span></div></li>')
    shown = (f'<p class="muted small">Showing {len(rows)} of {s["series"]:,} versions'
             + (", the top 200 for this sort" if len(rows) >= 200 else "") + '.</p>')
    note = ('<p class="muted small">Prices in your display currency, each sale converted at its own date. Typical sold is '
            'sold evidence only; asks come from the newest nightly snapshot and never move it. The 1 yr column is '
            'PriceCharting\'s own weekly sold data, fetched by a pinned sweep of this machine '
            '(<code>python -m cardman.pc sweep</code>) and shown in your currency at today\'s rate. The grading strip '
            'uses your cost profile: inbound shipping, the PSA fee, and the selling fees you actually pay.</p>')
    return head + chips + best + flip + cols + f'<ul class="mlist">{"".join(body)}</ul>' + shown + note


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
