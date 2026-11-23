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
.mhead{display:grid;grid-template-columns:minmax(0,1.55fr) 118px 118px 160px 124px 100px;gap:12px;
padding:9px 16px 9px 16px;position:sticky;top:0;z-index:6;margin:0 0 8px;
font-size:10.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--fg2);
background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
border-bottom:1px solid var(--line)}
.mhead .cc{display:inline-flex;align-items:center;gap:6px;cursor:help;justify-self:start}
.mhead .cc::before{content:"";width:6px;height:6px;border-radius:2px;background:var(--muted);flex:none}
.mhead .cc.typ::before{background:var(--fg2)}
.mhead .cc.pace::before{background:var(--teal)}
.mhead .cc.ask::before{background:var(--teal-deep)}
.mhead .cc.pc::before{background:#b98a1e}
.mhead .cc.trend::before{background:#c4553b}
@media(max-width:900px){.mhead{display:none}}
.move-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:6px 0 14px}
.move-grid h3{margin:0 0 4px;font-size:13px;color:var(--fg2);text-transform:uppercase;letter-spacing:.04em}
ul.mini{list-style:none;margin:0;padding:0}ul.mini li{padding:3px 0;font-size:13px;border-bottom:1px solid var(--line)}
ul.mini li:last-child{border-bottom:0}.up{color:var(--teal-deep);font-weight:600}.down{color:#b3261e;font-weight:600}
@media(max-width:760px){.move-grid{grid-template-columns:1fr}}
.mk-top{display:flex;align-items:center;justify-content:space-between;gap:10px 16px;flex-wrap:wrap;margin:0 0 14px}
.mk-top h1{margin:0}
.mk-top .mk-games{margin:0}
.mkph-bar{display:flex;gap:3px;height:30px;padding:3px;border-radius:10px;background:var(--soft);border:1px solid var(--line);
margin:0 0 10px;max-width:860px}
.mkph-bar a{display:block;min-width:5px;border-radius:6px;transition:filter .15s;position:relative;
box-shadow:inset 0 1px 0 rgba(255,255,255,.35),inset 0 -10px 14px rgba(0,0,0,.10)}
.mkph-bar a:hover{filter:brightness(1.12)}
.mkph-bar a:focus-visible{outline:2px solid var(--fg);outline-offset:1px}
.p-up{background:var(--teal)}.p-down{background:#c4553b}.p-hold{background:#c9c2b4}.p-none{background:#e0d9ca}
.mkph-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}
.mtile{display:inline-flex;align-items:center;gap:8px;padding:5px 12px 5px 10px;border:1px solid var(--line);border-radius:10px;
background:var(--surface);text-decoration:none;color:var(--fg);transition:box-shadow .15s,transform .15s,border-color .15s}
.mtile:hover{box-shadow:var(--shadow);transform:translateY(-1px)}
.mtile:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
.mtile b{font-size:16px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.mtile small{font-size:10px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.mtile i{flex:none;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent}
.mtile.up i{border-bottom:8px solid var(--teal)}
.mtile.down i{border-top:8px solid #c4553b}
.mtile.hold i{width:10px;height:2.5px;border:0;border-radius:2px;background:var(--fg2)}
.mtile.none i{width:8px;height:8px;border:1.5px solid var(--muted);border-radius:50%}
.mtile.on{background:var(--fg);border-color:var(--fg);color:var(--surface)}
.mtile.on small{color:color-mix(in srgb,var(--surface) 70%,transparent)}
.mtile.deal{border-style:dashed;border-color:color-mix(in srgb,var(--teal) 45%,var(--line))}
.mtile.deal b{color:var(--teal-deep)}
.mtile.grade{border-style:dashed;border-color:color-mix(in srgb,#b98a1e 45%,var(--line))}
.mtile.grade b{color:#8a6614}
.mtile i.gd{width:8px;height:8px;border:0;border-radius:2px;background:#b98a1e;transform:rotate(45deg)}
.mkstat{display:inline-flex;gap:16px;margin-left:auto;font-size:12px;color:var(--fg2);align-items:baseline}
.mkstat b{font-size:15px;color:var(--fg);font-variant-numeric:tabular-nums;margin-right:3px}
@media(max-width:700px){.mkstat{margin-left:0}}
.mkproof{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:2px}
.mkproof .pl{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin-right:2px}
.mkproof .sp{display:inline-flex;align-items:baseline;gap:5px;border:1px solid var(--line);border-radius:999px;
padding:2px 8px;background:var(--soft);font-size:11.5px;color:var(--fg2)}
.mkproof .sp b{color:var(--fg);font-variant-numeric:tabular-nums;font-size:11.5px}
.mkproof .sp small{font-size:10.5px;color:var(--muted)}
.mkproof .sp.none{border-style:dashed;background:none}
.mksboard{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:26px 0 8px;padding:0 2px}
.mksboard h2{margin:0;font-size:18px;letter-spacing:-.01em}
.mksboard a{margin-left:auto;font-size:12.5px;font-weight:700;color:var(--teal-deep);text-decoration:none}
.mksboard a:hover{text-decoration:underline}
.mlist{list-style:none;margin:0 0 10px;padding:0;display:grid;gap:8px}
.mrow{display:grid;grid-template-columns:minmax(0,1.55fr) 118px 118px 160px 124px 100px;gap:12px;align-items:center;
background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:12px;
padding:9px 14px 9px 12px;transition:box-shadow .2s,border-color .2s,transform .2s}
.mrow:hover{box-shadow:var(--shadow);transform:translateY(-1px)}
.mrow.up{border-left-color:var(--teal)}.mrow.down{border-left-color:#c4553b}
.mmain{min-width:0;display:flex;gap:12px;align-items:center}
.mthumb{flex:none;width:52px;height:73px;border-radius:7px;overflow:hidden;background:var(--soft);display:block;
border:1px solid var(--line);box-shadow:0 2px 6px rgba(15,30,45,.14)}
.mthumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .25s ease}
.mrow:hover .mthumb img{transform:scale(1.06)}
.mthumb.ph{background:repeating-linear-gradient(135deg,var(--soft) 0 6px,var(--surface) 6px 12px);border:0;box-shadow:none}
.mid{min-width:0}
.mnamewrap{display:flex;align-items:center;gap:7px;min-width:0}
.mnamewrap .mname{min-width:0}
.mpile{display:inline-flex;flex:none;opacity:0;transition:opacity .15s}
.mrow:hover .mpile,.mpile:focus-within{opacity:1}
.mpile .pbtn{width:22px;height:22px;font-size:12px;background:var(--surface)}
@media(hover:none){.mpile{opacity:1}}
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
.mtrend{display:block;min-width:0}
.slp{flex:none;display:block;overflow:visible}
.slp .ln{fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}
.slp .d0{fill:currentColor;opacity:.4}.slp .d1{fill:currentColor}
.mtrend .slp{color:var(--fg2)}
.mtrend.rising .slp{color:var(--teal)}
.mtrend.falling .slp{color:#c4553b}
.mtrend.steady .slp{color:#8a836f}
.mtrend.none .slp{color:var(--muted)}
.mtrend.none .d1{fill:none}
.mtrend b{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:800;white-space:nowrap}
.mtrend b i{flex:none;width:0;height:0;border-left:4.5px solid transparent;border-right:4.5px solid transparent}
.mtrend.rising b{color:var(--teal-deep)}.mtrend.falling b{color:#a2452f}.mtrend.steady b,.mtrend.none b{color:var(--fg2)}
.mtrend.rising b i{border-bottom:7px solid var(--teal)}
.mtrend.falling b i{border-top:7px solid #c4553b}
.mtrend.steady b i{width:8px;height:2.5px;border:0;background:var(--fg2);border-radius:2px}
.mtrend.none b i{width:7px;height:7px;border:1.5px solid var(--muted);border-radius:50%}
.mtrend>span{display:block;font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:1px;color:var(--fg)}
.mtrend small{display:block;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fbar{display:flex;flex-wrap:wrap;gap:8px 18px;margin:2px 0 6px}
.fsec{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.flab{font-size:10.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-right:2px}
.fbar .fchip b{font-weight:700;color:inherit;opacity:.65;margin-left:6px;font-variant-numeric:tabular-nums;font-size:11.5px}
.fbar .fchip.on b{opacity:.85}
.fnote{font-size:12px;color:var(--muted);margin:0 0 14px;max-width:980px}
.mpc{min-width:0;display:block}.mpc svg{display:block;width:116px;height:34px;overflow:visible}
a.mpcl{text-decoration:none;color:inherit;border-radius:6px}
a.mpcl:hover small,a.mpcl:focus-visible small{color:var(--teal-deep)}
.mpc .pline{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.mpc.up .pline{stroke:var(--teal)}.mpc.down .pline{stroke:#c4553b}
.mpc .pdot{stroke:none}.mpc.up .pdot{fill:var(--teal)}.mpc.down .pdot{fill:#c4553b}
.mpc small{display:block;font-size:11.5px;color:var(--muted);margin-top:2px;font-variant-numeric:tabular-nums;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mpc .pcnone{font-size:13px;color:var(--muted)}
small.cl{display:none}
@media(max-width:900px){.mrow{grid-template-columns:1fr 1fr;gap:10px 16px}
.mmain{grid-column:1/-1}
small.cl{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:2px;
font-weight:600}}
"""


def _grade(gkey: str) -> str:
    gc, _, gr = (gkey or "|").partition("|")
    return f"{gc} {gr}".strip() if gc else ""


def _slope(lab: str | None, pct: float | None) -> str:
    """Two points, first PriceCharting week on the left and last on the right. The angle is the size of the
    year move; a dotted flat line with a hollow end is no call at all."""
    if lab is None:
        return ('<svg class="slp none" width="58" height="20" viewBox="0 0 58 20" aria-hidden="true">'
                '<path class="ln" d="M6 13 H49" stroke-dasharray="2 3"/>'
                '<circle class="d1" cx="49" cy="13" r="2.6" fill="none" stroke="currentColor"/></svg>')
    dy = max(2.5, min(10.0, abs(pct or 0) / 5.0))
    y1 = 13.0
    y2 = 13.0 - dy if lab == "rising" else (13.0 + dy if lab == "falling" else 13.0)
    return (f'<svg class="slp {lab}" width="58" height="20" viewBox="0 0 58 20" aria-hidden="true">'
            f'<path class="ln" d="M6 {y1:.1f} L49 {y2:.1f}"/>'
            f'<circle class="d0" cx="6" cy="{y1:.1f}" r="2.2"/>'
            f'<circle class="d1" cx="49" cy="{y2:.1f}" r="3"/></svg>')


def _spark(points: list, label: str, tip: str, href: str | None = None) -> str:
    """One year of PriceCharting monthly sold points, drawn small. Colour is the direction of the year, the dot is today."""
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


def _filters(qs: dict, sort: str, min_sales: str) -> str:
    """Two named groups under the trend tiles, each chip carrying its count, so a filter says what it keeps
    before it is clicked."""
    def href(key, val):
        q = {k: v for k, v in qs.items() if k not in ("msg", "err", key)}
        if val not in (None, "all", ""):
            q[key] = val
        q = {k: v for k, v in q.items() if (k, v) not in (("label", "all"), ("min", "0"), ("sort", "move"))}
        return "/market" + ("?" + e(urlencode(q)) if q else "")

    def chip(key, val, text, current, n=None):
        on = current == val
        b = f'<b>{n:,}</b>' if n is not None else ""
        return f'<a class="fchip{" on" if on else ""}" href="{href(key, val)}">{text}{b}</a>'

    evidence = [chip("min", None, "Any history", min_sales),
                chip("min", "3", "3+ sales", min_sales), chip("min", "6", "6+ sales", min_sales)]
    order = [chip("sort", "move", "12-mo PC move", sort), chip("sort", "edge", "Under typical", sort),
             chip("sort", "sales", "Selling fast", sort), chip("sort", "spread", "Ask gap", sort),
             chip("sort", "recent", "Newest sale", sort)]
    groups = (("Evidence", evidence), ("Order", order))
    return ('<div class="fbar">' + "".join(
        f'<div class="fsec"><span class="flab">{e(name)}</span>{"".join(ch)}</div>' for name, ch in groups)
        + '</div>')


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


def _ask_links(st, now: datetime, home: str, pids: list[str]) -> tuple[dict[tuple[str, str], str],
                                                                       dict[tuple[str, str], str],
                                                                       dict[tuple[str, str], str]]:
    """The cheapest live listing behind each ask, same evidence rules as the nightly snapshot: engine 95% on the
    card, 90% on the printing, no lots, no ended listings, nothing unseen for three days. Returns the listing
    URL, its listing id, and its store key, so a section can show the seller's own photo or file the listing."""
    from decimal import Decimal

    from .fx import FxTable
    from .history import ASK_CARD_PROB, ASK_PRINTING_PROB
    from .listing_parser import _GRADE_RE
    if not pids or not {"card_listing", "listing"} <= _tables(st):
        return {}, {}, {}
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=3))
    except Exception:
        fx = None
    fresh = (now - timedelta(days=3)).isoformat()
    marks = ",".join("?" * len(pids))
    rows = st.db.execute(
        "SELECT c.printing_id, l.price, l.currency, l.data_json, l.title, l.url, l.listing_id, l.key FROM card_listing c "
        f"JOIN listing l ON l.key=c.key WHERE c.printing_id IN ({marks}) AND c.card_prob>=? AND c.printing_prob>=? "
        "AND c.lot=0 AND l.kind='ask' AND c.status<>'REJECTED' AND l.ended_at IS NULL AND l.last_seen>=?",
        (*pids, ASK_CARD_PROB, ASK_PRINTING_PROB, fresh))
    best: dict[tuple[str, str], tuple[float, str, str | None, str]] = {}
    for pid, price, cur, dj, title, url, lid, lkey in rows:
        if not url:
            continue
        try:
            v = float(price) if cur == home else (float(fx.convert(Decimal(str(price)), cur, home, now)) if fx else None)
        except Exception:
            continue
        if v is None:
            continue
        data = json.loads(dj or "{}")
        if not data.get("grade_company"):
            # A played copy is not a cheap Near Mint copy: restate the ask to NM on the same ladder the sales use.
            from . import conditions
            cc = conditions.normalize(data.get("condition"))
            if cc and cc != "NM":
                v = conditions.adjust(v, cc, "NM")
        gc, gr = data.get("grade_company"), data.get("grade")
        if not gc:
            m = _GRADE_RE.search(title or "")
            if m:
                gc, gr = m.group(1).upper().replace("BECKETT", "BGS"), m.group(2)
            elif "grad" in (data.get("condition") or "").lower():
                continue
        key = (pid, f"{gc or ''}|{gr or ''}")
        if key not in best or v < best[key][0]:
            best[key] = (v, url, lid, lkey)
    return ({k: url for k, (_v, url, _l, _k) in best.items()},
            {k: lid for k, (_v, _url, lid, _k) in best.items() if lid},
            {k: lkey for k, (_v, _url, _l, lkey) in best.items() if lkey})


def _actionable(st, game: str, now: datetime, home: str, cur: str, rows: list[dict]) -> dict:
    """The numbers both action sections spend: under-typical deals, raw→PSA 10 picks, ask links and listing
    ids, PriceCharting rows, and the cost constants."""
    from .engine import load_profile
    M = Money(st, home, cur, now)

    def fair_of(r):
        return r["median_30"] if (r["n_30"] or 0) >= 3 else r["median"]

    try:
        profile = load_profile()
    except Exception:
        profile = {}
    route = dict((profile.get("routes") or {}).get("INTL") or {})
    exit_c = dict(profile.get("exit") or {})
    k_buy = 1 + float(route.get("payment_fx_spread_pct") or 0)
    ship_in = float(route.get("inbound_shipping") or 0)
    fixed = ship_in + float((profile.get("grading") or {}).get("cost_home") or 0)
    fee_pct = (float(exit_c.get("platform_fee_pct") or 0) + float(exit_c.get("returns_reserve_pct") or 0)
               + float(exit_c.get("payout_fx_spread_pct") or 0))
    ship_out = float(exit_c.get("outbound_shipping") or 0)
    target = float(profile.get("target_return") or 0)

    def flip_profit(ask_home, fair_home):
        # Raw in, raw out: exit fees and the buy-side spread. Inbound/outbound shipping is padding on a comparison.
        return fair_home * (1 - fee_pct) - k_buy * ask_home

    deals = []
    for d in trends.board(st, game=game, sort="edge", min_sales=3, limit=120):
        f = fair_of(d)
        ask = d["ask_low"]
        if not ask or not f or (f - ask) / f < 0.02 or not (f / 3 <= ask <= f * 3):
            continue
        p = flip_profit(ask, f)
        if p > 0:
            deals.append((p, d))
    deals.sort(key=lambda x: -x[0])
    under_n = len(deals)
    deals = [d for _p, d in deals[:6]]
    pids = sorted({r["printing_id"] for r in rows} | {d["printing_id"] for d in deals})
    links, items, keys = _ask_links(st, now, home, pids)
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
    picks = [("now",) + b for b in buys[:4]] + [("wait",) + w for w in watches[:2]]

    # Proof: the newest real sales behind each card the action sections name, straight from the store.
    from .card_market import site_of
    from .ui_home import SITE_NAME
    proof: dict[str, list] = {}
    named = sorted({d["printing_id"] for d in deals} | {p[2]["printing_id"] for p in picks})
    if named:
        marks = ",".join("?" * len(named))
        for pid, dj, when, src, ev in st.db.execute(
                "SELECT p.printing_id, o.data_json, o.event_time, o.source, o.evidence_type "
                "FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
                "o.evidence_type IN ('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') AND "
                f"p.printing_id IN ({marks}) ORDER BY o.event_time DESC", named):
            got = proof.setdefault(pid, [])
            if len(got) >= 3:
                continue
            try:
                dj = json.loads(dj or "{}")
                amt, sc = dj.get("original_amount"), dj.get("currency") or "USD"
                if isinstance(amt, dict):
                    amt = amt.get("$dec") or amt.get("$float")
                if amt is None:
                    continue
            except Exception:
                continue
            v = None
            try:
                if M.fx is not None:
                    v = float(M.fx.convert(Decimal(str(amt)), sc, M.home, datetime.fromisoformat(when)))
            except Exception:
                v = None
            got.append((M(v) if v is not None else f"{float(amt):,.2f} {sc}", _short_day(when[:10]),
                        SITE_NAME.get(site_of(src), "Other"), ev))

    def proof_sales(pid):
        got = proof.get(pid) or []
        if not got:
            return '<span class="sp none">no stored sales</span>'
        return "".join(
            f'<span class="sp" title="{e(site)} · {e(day)} · {e(ev.replace("_", " ").lower())}">'
            f'<b>{e(price)}</b><small>{e(day)}</small></span>' for price, day, site, ev in got)

    return {"deals": deals, "under_n": under_n, "picks": picks, "links": links, "items": items, "keys": keys,
            "pcrows": pcrows, "fair_of": fair_of, "usd_home": usd_home, "pc_money": pc_money,
            "vol_per_month": vol_per_month, "flip_profit": flip_profit, "proof_sales": proof_sales,
            "k_buy": k_buy, "fixed": fixed, "fee_pct": fee_pct, "ship_out": ship_out, "target": target}


def view_market(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo",
                cards=None, pkm=None, token: str = "") -> str:
    M = Money(st, home, cur, now)
    if "price_trend" not in _tables(st):
        return ('<h1>Market</h1><p class="empty">The market board fills once cards start selling. The engine writes it at '
                'the end of every pass, or run <code>python -m cardman.trends refresh</code>.</p>')
    label = qs.get("label", ["all"])[0]
    label = label if label in ("all", "rising", "falling", "steady", "none") else "all"
    sort = qs.get("sort", ["move"])[0]
    sort = {"move30": "move"}.get(sort) or (sort if sort in ("move", "edge", "sales", "spread", "recent") else "move")
    min_sales = qs.get("min", ["0"])[0]
    min_sales = int(min_sales) if min_sales.isdigit() else 0
    s = trends.summary(st, game)
    rows = trends.board(st, game=game, label=None if label == "all" else label, min_sales=min_sales, sort=sort, limit=200)
    name = {"ygo": "Yu-Gi-Oh!", "pkm": "Pokémon"}.get(game, game)
    total = s["series"] or 1

    def pct(n):
        return max(0.0, n / total * 100)

    A = _actionable(st, game, now, home, cur, rows)
    under_n, picks = A["under_n"], A["picks"]
    now_href = "/pkm/market/now" if game == "pkm" else "/market/now"

    def jump(lab):
        q = {k: v for k, v in qs.items() if k not in ("msg", "err", "label")}
        if lab not in ("all", label):
            q["label"] = lab
        return "/market" + ("?" + e(urlencode(q)) if q else "")

    pulse = ""
    if s["series"]:
        tiles = (("rising", "up", "p-up", s["rising"], "Climbing"),
                 ("falling", "down", "p-down", s["falling"], "Slipping"),
                 ("steady", "hold", "p-hold", s["steady"], "Holding"),
                 ("none", "none", "p-none", s["no_trend"], "Thin"))
        rule = ("One rule on every row: PriceCharting's monthly sold points for the same grade over 12 months. "
                "Climbing: up 5% or more. Slipping: down 5% or more. Holding: inside 5%. A series too young "
                "for a year is Thin, and says how many points it has.")
        pulse = ('<div class="mkph-bar" role="img" aria-label="Market split by trend" title="' + e(rule) + '">'
                 + "".join(f'<a class="{bc}" style="width:{pct(n):.1f}%" href="{jump(l)}" '
                           f'title="{n:,} {w} — show only these"></a>' for l, _c, bc, n, w in tiles if n)
                 + '</div><div class="mkph-row">'
                 + "".join(f'<a class="mtile {c}{" on" if label == l else ""}" href="{jump(l)}"'
                           + (' aria-current="true"' if label == l else "")
                           + f' aria-label="{w}: {n:,} versions"'
                           + f' title="{w}: {n:,} versions. A click keeps only these; a second click clears it.">'
                           f'<i aria-hidden="true"></i><b>{n:,}</b><small class="sr">{w}</small></a>' for l, c, _bc, n, w in tiles)
                 + (f'<a class="mtile deal" href="{now_href}" title="{under_n} live asks under the typical sold price '
                    f'after fees. Open the Now zone for the listings."><b>{under_n}</b><small>under typical</small></a>'
                    if under_n else "")
                 + (f'<a class="mtile grade" href="{now_href}" title="{len(picks)} raw copies an ask makes worth '
                    f'grading right now. Open the Now zone for the numbers."><i class="gd" aria-hidden="true"></i>'
                    f'<b>{len(picks)}</b><small>grading</small></a>' if picks else "")
                 + '<span class="mkstat">'
                 + f'<span><b>{s["with_asks"]:,}</b> listed</span>'
                 + f'<span><b>{s["n_30"]:,}</b> sold · 30d</span>'
                 + '</span></div>')
    tabs = ('<div class="mk-games" role="tablist" aria-label="Game">'
            + "".join(f'<a role="tab"{ON_TAB if g == game else ""} href="/game?{e(urlencode({"g": g, "back": "/market"}))}">'
                      f'{e(lab)}</a>' for g, lab in (("ygo", "Yu-Gi-Oh!"), ("pkm", "Pokémon")))
            + '</div>')
    head = f'<div class="mk-top"><h1>The {e(name)} market</h1>' + tabs + '</div>' + pulse
    chips = _filters({**qs, "sort": sort, "min": str(min_sales)}, sort, str(min_sales))
    cols = ('<div class="mhead">'
            '<span class="cc card">Card</span>'
            '<span class="cc typ" title="Median of this exact version\'s sales, in your currency. Uses the last 30 days '
            'when 3+ sales sit there, otherwise all stored sales. The line under it is the middle half (p25-p75).">Typical sold</span>'
            '<span class="cc pace" title="Copies sold per month. The line under it: newest sale and how many sales back '
            'the number.">Sales pace</span>'
            '<span class="cc ask" title="Cheapest live listing in this version\'s newest snapshot (dated on the row), '
            'the listing itself one click away, and its gap to the typical price. Supply: listed count divided by '
            'the monthly sales pace.">Cheapest ask</span>'
            '<span class="cc pc" title="PriceCharting\'s own monthly sold points for this grade (Ungraded for raw rows), '
            'last 12 months. The line under it is the year change. A verdict from a second market, not a copy of ours.">'
            '1 yr · PC</span>'
            '<span class="cc trend" title="PriceCharting\'s monthly sold points for this grade, first point to last '
            'over 12 months. Climbing 5%+, Slipping 5%-, else Holding. A series too young for a year reads Thin '
            'and names its point count.">Trend</span>'
            '</div>')
    name_of = _name_of(cards, pkm)
    links, pcrows = A["links"], A["pcrows"]
    pkeys = A["keys"]
    usd_home, pc_money = A["usd_home"], A["pc_money"]
    mback = "/pkm/market" if game == "pkm" else "/market"
    if token:
        from .pile import keys as pile_keys
        _piled = frozenset(pile_keys(st))
    else:
        _piled = frozenset()

    if not rows:
        return (head + chips + '<p class="empty">Nothing matches those filters yet. A version shows up once real sales '
                'of that exact printing and grade are stored.</p>')
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
        pcr = pcrows.get(pid)
        gs = pc.grade_series(pcr, gkey)
        kpts = len([p for p in gs[1] if p[1]]) if gs else 0
        # One rule for every row: PriceCharting's monthly sold points for this grade over 12 months. It is a
        # second market's own sales, so every row with a series gets the same call, however thin ours are.
        if r.get("pc_move") is not None:
            lab, pct = r["pc_move_label"], r["pc_move"]
            ev = f'{r["n_sales"]} sales · PC 12 mo'
            why = (f'PriceCharting\'s monthly sold points for this grade moved {pct:+.0f}% over 12 months, '
                   f'against {r["n_sales"]} sales of our own.')
        elif kpts:
            lab, pct = None, None
            ev = f'{r["n_sales"]} sales · PC {kpts} pt{"s" if kpts != 1 else ""}'
            why = f"PriceCharting's {gs[0]} series is only {kpts} weekly point{'s' if kpts != 1 else ''} old: too young for a year call."
        else:
            lab, pct = None, None
            ev = f"{r['n_sales']} sales · no series"
            why = "No PriceCharting series for this grade: nothing to compare."
        word = {"rising": "Climbing", "falling": "Slipping", "steady": "Holding"}.get(lab)
        cls = lab if lab in ("rising", "falling", "steady") else "none"
        row_cls = {"rising": "up", "falling": "down", "steady": "hold"}.get(lab, "none")
        if word is None:
            word, pct_txt = "Thin", f"n={r['n_sales']}"
        else:
            pct_txt = "flat" if abs(pct) < 1 else f"{pct:+.0f}%"
        meaning = {"rising": "Prices are climbing: a copy near typical is the buy.",
                   "falling": "Prices are slipping: wait, or demand well under typical.",
                   "steady": "Prices are holding: pay near typical, no rush."}.get(lab, "")
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
            seen_age = _days_since(r["ask_day"], now)
            if seen_age is not None and seen_age > 2:
                asub += f' · seen {seen_age}d ago'
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
        if gs and kpts >= 2:
            gname, gpts = gs
            cur_pc = pc.current(pcr, gname) or {}
            gchg = pc.year_change(gpts)
            now_usd = cur_pc.get("p") if cur_pc.get("p") is not None else gpts[-1][1]
            vol = (cur_pc.get("vol") or "").replace(" per ", "/")
            tip = (f"PriceCharting {gname}: {pc_money(now_usd)} now"
                   + (f", {gchg:+.0f}% over the year" if gchg is not None else "")
                   + (f"; {vol} recently" if vol else "") + ". Monthly sold points, 12 months.")
            pc_html = _spark(gpts, f"{gname} · {gchg:+.0f}%" if gchg is not None else gname, tip, pcr.get("url"))
        else:
            short = f'{kpts} point' if kpts else ("no series" if pcr else "not swept")
            pc_html = f'<span class="mpc"><span class="pcnone">—</span><small>{short}</small></span>'
        pkey = pkeys.get((pid, gkey))
        pf = ""
        if pkey and token:
            piled = pkey in _piled
            pf = (f'<form class="mpile" method="post" action="/pile/{"remove" if piled else "add"}">'
                  f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="key" value="{e(pkey)}">'
                  f'<input type="hidden" name="back" value="{e(mback)}">'
                  f'<button class="pbtn{" on" if piled else ""}" type="submit" '
                  f'title="{"In the pile - remove" if piled else "Set aside in the card pile"}" '
                  f'aria-label="{"Remove from the card pile" if piled else "Set aside in the card pile"}">'
                  f'{"✓" if piled else "＋"}</button></form>')
        body.append(
            f'<li class="mrow {row_cls}"><div class="mmain">{thumb}'
            f'<span class="mid"><span class="mnamewrap">{pf}{nm_html}</span>'
            f'<span class="mmeta"><span class="mcode">{e(r["label"])}</span>{pill}</span></span></div>'
            f'<div class="mfair"><small class="cl">Typical sold</small>'
            f'<b title="Middle of the {"last 30 days of" if use30 else ""} sales of this exact version: {n} sold between '
            f'{M(r["low"])} and {M(r["high"])}">{M(fair)}</b>'
            f'<small title="Middle half of all sales of this version">{n} sold · {rng}</small></div>'
            f'<div class="mturn"><small class="cl">Sales pace</small>'
            f'<b title="Copies sold per month across {n} stored sales">{pace_txt}<span> copies/mo</span></b>'
            f'<small>last {last_txt} · n={n}</small></div>'
            f'<div class="mask"><small class="cl">Cheapest ask</small>{ask_html}</div>'
            f'<div class="mpcell"><small class="cl">1 yr · PC</small>{pc_html}</div>'
            f'<div class="mtick"><small class="cl">Trend</small>'
            f'<span class="mtrend {cls}" title="{e(why)} {e(meaning)}" aria-label="{e(word)}: {e(pct_txt)}">'
            f'<b>{_slope(lab, pct if lab else None)}'
            f'<span class="sr">{word}</span></b><span>{e(pct_txt)}</span><small>{e(ev)}</small></span></div></li>')
    shown = (f'<p class="muted small">Showing {len(rows)} of {s["series"]:,} versions'
             + (", the top 200 for this sort" if len(rows) >= 200 else "") + '.</p>')
    note = ('<p class="muted small">Typical sold is sold evidence only; asks are each version\'s newest snapshot '
            'in the last month, dated on the row, and never move it. 1 yr · PC is PriceCharting\'s monthly sold '
            'points for the same grade, in your currency at today\'s rate.</p>')
    return (head + chips
            + f'<div class="mksboard"><h2>Every printing we can price</h2>'
            f'<a href="{now_href}">Deals and grading →</a></div>'
            + cols + f'<ul class="mlist">{"".join(body)}</ul>' + shown + note)


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
        blocks.append('<div><h3>' + _slope("rising", 25) + '<span class="sr">Rising</span></h3>'
                      '<ul class="mini">' + "".join(li(r) for r in up) + "</ul></div>")
    if down:
        blocks.append('<div><h3>' + _slope("falling", 25) + '<span class="sr">Falling</span></h3>'
                      '<ul class="mini">' + "".join(li(r) for r in down) + "</ul></div>")
    if not blocks:
        blocks.append('<p class="empty">No version has enough dated sales for a direction yet.</p>')
    return ('<div class="home-h"><h2>Market movement</h2><a href="/market">Whole market →</a></div>'
            f'<p class="muted small">{s["series"]:,} versions priced from real sales · {s["rising"]:,} rising · '
            f'{s["falling"]:,} falling · {s["n_30"]:,} sales in the last 30 days.</p>'
            f'<div class="move-grid">{"".join(blocks)}</div>')
