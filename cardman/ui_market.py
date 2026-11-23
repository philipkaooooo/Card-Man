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
.mhead{display:grid;grid-template-columns:minmax(0,1.55fr) 118px 118px 160px 124px 100px;gap:12px;padding:4px 16px 8px;
font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line);margin:0 0 10px}
.mhead span{cursor:help;border-bottom:1px dotted color-mix(in srgb,var(--muted) 45%,transparent);justify-self:start}
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
.mkstat{display:inline-flex;gap:16px;margin-left:auto;font-size:12px;color:var(--fg2);align-items:baseline}
.mkstat b{font-size:15px;color:var(--fg);font-variant-numeric:tabular-nums;margin-right:3px}
@media(max-width:700px){.mkstat{margin-left:0}}
.mkjump{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 6px}
.mkjump a{font-size:12.5px;font-weight:700;color:var(--fg2);text-decoration:none;padding:4px 12px;border-radius:999px;
border:1px solid var(--line);background:var(--surface)}
.mkjump a:hover{color:var(--teal-deep);border-color:color-mix(in srgb,var(--teal) 40%,var(--line))}
.mks{margin:18px 0 26px;padding:16px 18px;border-radius:18px;background:color-mix(in srgb,var(--surface) 82%,var(--soft))}
.mks#under{border-left:4px solid var(--teal)}
.mks.grade{border-left:4px solid #b98a1e}
.mks-h{margin:0 0 12px}
.mks-h h2{margin:0 0 4px;font-size:21px;letter-spacing:-.01em}
.mks-h p{margin:0;color:var(--fg2);font-size:13.5px;max-width:760px}
.mkgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.mkcard{display:flex;gap:13px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px;
transition:box-shadow .2s,transform .2s}
.mkcard:hover{box-shadow:var(--shadow);transform:translateY(-1px)}
.mkimg{flex:none;width:82px;height:115px;border-radius:8px;overflow:hidden;background:var(--soft);display:block;
border:1px solid var(--line);box-shadow:0 2px 8px rgba(15,30,45,.16)}
.mkimg img{width:100%;height:100%;object-fit:cover;display:block}
.mkimg.ph{background:repeating-linear-gradient(135deg,var(--soft) 0 6px,var(--surface) 6px 12px);border:0;box-shadow:none}
.mkbody{min-width:0;flex:1;display:flex;flex-direction:column;gap:5px}
.mkname{font-weight:700;font-size:15.5px;color:var(--fg);text-decoration:none;line-height:1.2;
overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.mkname:hover{color:var(--teal-deep)}
.mkmeta{font-size:11.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mkvals{display:flex;flex-wrap:wrap;gap:6px 16px;margin:2px 0 2px}
.mkv small{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}
.mkv b{font-size:16px;font-variant-numeric:tabular-nums;line-height:1.2}
.mkv b.pos{color:var(--teal-deep)}
.mkv em{font-style:normal;font-size:11px;color:var(--fg2)}
.mkproof{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:2px}
.mkproof .pl{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin-right:2px}
.mkproof .sp{display:inline-flex;align-items:baseline;gap:5px;border:1px solid var(--line);border-radius:999px;
padding:2px 8px;background:var(--soft);font-size:11.5px;color:var(--fg2)}
.mkproof .sp b{color:var(--fg);font-variant-numeric:tabular-nums;font-size:11.5px}
.mkproof .sp small{font-size:10.5px;color:var(--muted)}
.mkproof .sp.none{border-style:dashed;background:none}
.mkcta{margin-top:auto;padding-top:4px;font-size:12.5px;font-weight:700;color:var(--teal-deep);text-decoration:none}
.mks.grade .mkcta{color:#8a6614}
.mkcta:hover{text-decoration:underline}
.mkcta.muted{color:var(--muted);font-weight:600}
.tag{display:inline-block;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;padding:2px 6px;
border-radius:4px;margin-right:7px;vertical-align:1px}
.tag.now{background:var(--teal);color:#fff}.tag.wait{background:color-mix(in srgb,#8a6614 16%,var(--surface));color:#8a6614}
.mksboard{font-size:18px;margin:26px 0 10px;letter-spacing:-.01em}
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
.mkgrid{grid-template-columns:1fr}
.mmain{grid-column:1/-1}
small.cl{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-bottom:2px;
font-weight:600}}
"""


def _grade(gkey: str) -> str:
    gc, _, gr = (gkey or "|").partition("|")
    return f"{gc} {gr}".strip() if gc else ""


def _slope(lab: str | None, pct: float | None, ours: bool) -> str:
    """A two-point slope: the middle of the prior sales on the left, the middle of the newest on the right.
    The angle is the size of the move; solid is our own sales, dashed is PriceCharting standing in; a dotted
    flat line with a hollow end is no direction at all."""
    if lab is None:
        return ('<svg class="slp none" width="58" height="20" viewBox="0 0 58 20" aria-hidden="true">'
                '<path class="ln" d="M6 13 H49" stroke-dasharray="2 3"/>'
                '<circle class="d1" cx="49" cy="13" r="2.6" fill="none" stroke="currentColor"/></svg>')
    dy = max(2.5, min(10.0, abs(pct or 0) / 5.0))
    y1 = 13.0
    y2 = 13.0 - dy if lab == "rising" else (13.0 + dy if lab == "falling" else 13.0)
    dash = '' if ours else ' stroke-dasharray="3 2"'
    return (f'<svg class="slp {lab}" width="58" height="20" viewBox="0 0 58 20" aria-hidden="true">'
            f'<path class="ln" d="M6 {y1:.1f} L49 {y2:.1f}"{dash}/>'
            f'<circle class="d0" cx="6" cy="{y1:.1f}" r="2.2"/>'
            f'<circle class="d1" cx="49" cy="{y2:.1f}" r="3"/></svg>')


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
    order = [chip("sort", "move", "Newest-vs-prior move", sort), chip("sort", "edge", "Under typical", sort),
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
                                                                       dict[tuple[str, str], str]]:
    """The cheapest live listing behind each ask, same evidence rules as the nightly snapshot: engine 95% on the
    card, 90% on the printing, no lots, no ended listings, nothing unseen for three days. Returns the listing
    URL and its listing id, so a section can show the seller's own photo."""
    from decimal import Decimal

    from .fx import FxTable
    from .history import ASK_CARD_PROB, ASK_PRINTING_PROB
    from .listing_parser import _GRADE_RE
    if not pids or not {"card_listing", "listing"} <= _tables(st):
        return {}, {}
    try:
        fx = FxTable(st.fx_rates(), max_age=timedelta(days=3))
    except Exception:
        fx = None
    fresh = (now - timedelta(days=3)).isoformat()
    marks = ",".join("?" * len(pids))
    rows = st.db.execute(
        "SELECT c.printing_id, l.price, l.currency, l.data_json, l.title, l.url, l.listing_id FROM card_listing c "
        f"JOIN listing l ON l.key=c.key WHERE c.printing_id IN ({marks}) AND c.card_prob>=? AND c.printing_prob>=? "
        "AND c.lot=0 AND l.kind='ask' AND c.status<>'REJECTED' AND l.ended_at IS NULL AND l.last_seen>=?",
        (*pids, ASK_CARD_PROB, ASK_PRINTING_PROB, fresh))
    best: dict[tuple[str, str], tuple[float, str, str | None]] = {}
    for pid, price, cur, dj, title, url, lid in rows:
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
            best[key] = (v, url, lid)
    return ({k: url for k, (_v, url, _l) in best.items()},
            {k: lid for k, (_v, _url, lid) in best.items() if lid})


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
    links, items = _ask_links(st, now, home, pids)
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
    return {"deals": deals, "under_n": under_n, "picks": picks, "links": links, "items": items,
            "pcrows": pcrows, "fair_of": fair_of, "usd_home": usd_home, "pc_money": pc_money,
            "vol_per_month": vol_per_month, "flip_profit": flip_profit,
            "k_buy": k_buy, "fixed": fixed, "fee_pct": fee_pct, "ship_out": ship_out, "target": target}


def view_market(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo",
                cards=None, pkm=None) -> str:
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
    deals, under_n, picks = A["deals"], A["under_n"], A["picks"]
    fair_of = A["fair_of"]

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
        rule = ("One rule on every row: the middle of its newest sales against the middle of the ones just before "
                "them. Climbing: up 5% or more. Slipping: down 5% or more. Holding: inside 5%. Too few sales "
                "here, PriceCharting's 12-month series for the same grade answers; Thin means neither could.")
        pulse = ('<div class="mkph-bar" role="img" aria-label="Market split by trend" title="' + e(rule) + '">'
                 + "".join(f'<a class="{bc}" style="width:{pct(n):.1f}%" href="{jump(l)}" '
                           f'title="{n:,} {w} — show only these"></a>' for l, _c, bc, n, w in tiles if n)
                 + '</div><div class="mkph-row">'
                 + "".join(f'<a class="mtile {c}{" on" if label == l else ""}" href="{jump(l)}"'
                           + (' aria-current="true"' if label == l else "")
                           + f' aria-label="{w}: {n:,} versions"'
                           + f' title="{w}: {n:,} versions. A click keeps only these; a second click clears it.">'
                           f'<i aria-hidden="true"></i><b>{n:,}</b><small class="sr">{w}</small></a>' for l, c, _bc, n, w in tiles)
                 + (f'<a class="mtile deal" href="#under" title="{under_n} live asks under the typical sold price '
                    f'after fees."><b>{under_n}</b><small>under typical</small></a>' if under_n else "")
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
            '<span title="The middle of this row\'s newest sales against the middle of the sales just before them - '
            'same count on each side, up to 5. Climbing 5%+, Slipping 5%-, else Holding. Too few sales, '
            'PriceCharting\'s 12-month series for that grade answers instead; Thin means neither could.">'
            'Trend</span>'
            '</div>')
    name_of = _name_of(cards, pkm)
    links, items, pcrows = A["links"], A["items"], A["pcrows"]
    usd_home, pc_money, vol_per_month = A["usd_home"], A["pc_money"], A["vol_per_month"]
    flip_profit = A["flip_profit"]
    fixed, fee_pct, k_buy = A["fixed"], A["fee_pct"], A["k_buy"]

    # Proof: the newest real sales behind each card named in the two sections, straight from the store.
    from .card_market import site_of
    from .ui_home import SITE_NAME
    proof: dict[str, list] = {}
    named = [d["printing_id"] for d in deals] + [p[2]["printing_id"] for p in picks]
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

    def proof_html(pid):
        got = proof.get(pid) or []
        if not got:
            return '<span class="sp none">no stored sales</span>'
        return "".join(
            f'<span class="sp" title="{e(site)} · {e(day)} · {e(ev.replace("_", " ").lower())}">'
            f'<b>{e(price)}</b><small>{e(day)}</small></span>' for price, day, site, ev in got)

    def media(pid, nm):
        key = _card_key(pid)
        thumb = (f'<a class="mkimg" href="{e(card_href(key))}" tabindex="-1" aria-hidden="true">{img(key, "small", nm, "")}</a>'
                 if key else '<span class="mkimg ph" aria-hidden="true"></span>')
        name_html = (f'<a class="mkname" href="{e(card_href(key))}">{e(nm)}</a>' if key
                     else f'<span class="mkname">{e(nm)}</span>')
        return thumb, name_html, key

    if deals:
        cards = []
        for d in deals:
            pid = d["printing_id"]
            nm = name_of(pid) or d["label"].split(" · ")[0]
            thumb, name_html, key = media(pid, nm)
            f = fair_of(d)
            ask = d["ask_low"]
            r = d["n_sales"]
            p = flip_profit(ask, f)
            pct = (f - ask) / f * 100
            url = links.get((pid, d["gkey"] or "|"))
            cta = (f'<a class="mkcta" href="{e(url)}" target="_blank" rel="noreferrer noopener">Open the listing →</a>'
                   if url else '<span class="mkcta muted">Listing link expired</span>')
            cards.append(
                '<article class="mkcard">' + thumb + '<div class="mkbody">' + name_html
                + f'<div class="mkmeta">{e(d["label"])}</div>'
                + '<div class="mkvals">'
                + f'<div class="mkv"><small>ask now</small><b>{M(ask)}</b><em>{pct:.0f}% under</em></div>'
                + f'<div class="mkv"><small>sells for</small><b>{M(f)}</b><em>{r} sales</em></div>'
                + f'<div class="mkv"><small>left after fees</small><b class="pos">+{M(p)}</b><em>at typical</em></div>'
                + '</div>'
                + f'<div class="mkproof"><span class="pl">sold</span>{proof_html(pid)}</div>'
                + cta + '</div></article>')
        under_html = ('<section class="mks" id="under"><div class="mks-h"><h2>Under typical now</h2>'
                 '<p>Live asks below what the exact printing sells for, after the fees to sell it. '
                 'Each card carries its newest real sales as proof.</p></div>'
                 '<div class="mkgrid">' + "".join(cards) + '</div></section>')
    else:
        under_html = ('<section class="mks" id="under"><div class="mks-h"><h2>Under typical now</h2>'
                 '<p>No live ask sits under the typical sold price after fees right now. '
                 'The board below fills as asks come in.</p></div></section>')

    if picks:
        cards = []
        for kind, room, r, ask, net, psa in picks:
            pid = r["printing_id"]
            nm = name_of(pid) or r["label"].split(" · ")[0]
            thumb, name_html, key = media(pid, nm)
            vol = (psa.get("vol") or "")
            rate = vol_per_month(vol)
            per = (f"{rate:.0f}/mo" if rate and rate >= 10 else f"{rate:.1f}/mo" if rate else "rare")
            pop = (pcrows.get(pid) or {}).get("pop") or {}
            psa_pop = pop.get("psa") or []
            popn = psa_pop[9] if len(psa_pop) >= 10 else None
            f = fair_of(r)
            if kind == "now":
                all_in = k_buy * ask + fixed
                tag = '<span class="tag now">buy now</span>'
                href = links.get((pid, "|"))
                vals = (f'<div class="mkv"><small>raw ask</small><b>{M(ask)}</b><em>+{M(all_in - ask)} to grade</em></div>'
                        f'<div class="mkv"><small>all-in</small><b>{M(all_in)}</b><em>grading + shipping</em></div>'
                        f'<div class="mkv"><small>PSA 10 nets</small><b>{M(net)}</b><em>after fees</em></div>'
                        f'<div class="mkv"><small>profit</small><b class="pos">+{M(room)}</b><em>at the ask</em></div>')
                cta = (f'<a class="mkcta" href="{e(href)}" target="_blank" rel="noreferrer noopener">Open the raw listing →</a>'
                       if href else '<span class="mkcta muted">Ask snapshot only</span>')
            else:
                tag = '<span class="tag wait">wait for</span>'
                vals = (f'<div class="mkv"><small>pay at most</small><b>{M(ask)}</b>'
                        f'<em>raw · all-in {M(k_buy * ask + fixed)}</em></div>'
                        f'<div class="mkv"><small>PSA 10 nets</small><b>{M(net)}</b><em>after fees</em></div>')
                cta = (f'<a class="mkcta" href="{e(card_href(key))}">Watch the card →</a>' if key
                       else '<span class="mkcta muted">—</span>')
            cards.append(
                '<article class="mkcard grade">' + thumb + '<div class="mkbody">'
                + f'<div class="mkmeta">{tag}{e(r["label"])}</div>' + name_html
                + '<div class="mkvals">' + vals + '</div>'
                + '<div class="mkproof"><span class="pl">PSA 10</span>'
                + f'<span class="sp"><b>{pc_money(psa["p"])}</b><small>now · {per}</small></span>'
                + (f'<span class="sp"><b>{popn:,}</b><small>PSA pop</small></span>' if popn is not None else "")
                + f'<span class="sp"><b>{M(f)}</b><small>raw typical · {r["n_sales"]} sold</small></span>'
                + '</div>' + cta + '</div></article>')
        grade_html = ('<section class="mks grade" id="grade"><div class="mks-h"><h2>Buy raw → grade → sell PSA 10</h2>'
                 f'<p>Costs from your profile: {M(fixed)} fixed per card (inbound shipping + PSA fee), '
                 f'{fee_pct * 100:.1f}% to sell. PSA 10 must trade at least monthly, or it is a shelf, not a market.</p>'
                 '</div><div class="mkgrid">' + "".join(cards) + '</div></section>')
    else:
        grade_html = ('<section class="mks grade" id="grade"><div class="mks-h"><h2>Buy raw → grade → sell PSA 10</h2>'
                 '<p>No raw ask clears grading and selling fees with a live PSA 10 market behind it right now.</p>'
                 '</div></section>')
    jump = ('<nav class="mkjump" aria-label="Market sections"><a href="#under">Under typical</a>'
            '<a href="#grade">Raw → PSA 10</a><a href="#board">Full board</a></nav>')
    if not rows:
        return (head + chips + jump + under_html + grade_html
                + '<p class="empty">Nothing matches those filters yet. A version shows up once real sales of that exact '
                'printing and grade are stored.</p>')
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
        # One rule for every row: the middle of the newest sales against the middle of the ones just before them,
        # same count on each side. Too thin for that, PriceCharting's weekly series for the same grade; too thin
        # for both, the row says so.
        if r.get("move_recent") is not None:
            lab, pct = r["move_recent_label"], r["move_recent"]
            k = r["move_n"] or 0
            ev = f"newest {k} vs {k} before"
            why = (f'The middle of the newest {k} sales against the middle of the {k} before them: {pct:+.0f}%.')
        elif r.get("pc_move") is not None:
            lab, pct = r["pc_move_label"], r["pc_move"]
            k = 0
            ev = f'{r["n_sales"]} sales · PC 12 mo'
            why = (f'Only {r["n_sales"]} sales here. PriceCharting\'s weekly sold series for this grade moved '
                   f'{pct:+.0f}% over 12 months.')
        else:
            lab, pct, k = None, None, 0
            ev = f"{r['n_sales']} sales · need 4"
            why = "Fewer than four sales and no PriceCharting series: nothing to compare."
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
            cur_pc = pc.current(pcr, gname) or {}
            gchg = (gpts[-1][1] / gpts[0][1] - 1) * 100 if gpts[0][1] else None
            pc_v = usd_home(cur_pc.get("p")) if cur_pc.get("p") is not None else None
            if pc_v and fair and not (fair / 3 <= pc_v <= fair * 3):
                # The two markets are more than 3x apart: name the gap, do not draw it as this card's price history.
                pc_html = (f'<span class="mpc"><span class="pcnone">≠</span><small title="PriceCharting\'s '
                           f'{e(gname)} price is {M(pc_v)} against our {M(fair)} typical - more than 3x apart, so the '
                           f'line is not drawn">far from ours</small></span>')
            else:
                vol = (cur_pc.get("vol") or "").replace(" per ", "/")
                tip = (f"PriceCharting {gname}: {pc_money(gpts[-1][1])} now"
                       + (f", {gchg:+.0f}% over the year" if gchg is not None else "")
                       + (f"; {vol} recently" if vol else "") + ". Weekly points, 12 months.")
                pc_html = _spark(gpts, f"{gname} · {gchg:+.0f}%" if gchg is not None else gname, tip, pcr.get("url"))
        else:
            pc_html = '<span class="mpc"><span class="pcnone">—</span><small>not swept</small></span>'
        body.append(
            f'<li class="mrow {row_cls}"><div class="mmain">{thumb}'
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
            f'<span class="mtrend {cls}" title="{e(why)} {e(meaning)}" aria-label="{e(word)}: {e(pct_txt)}">'
            f'<b>{_slope(lab, pct if lab else None, r.get("move_recent") is not None)}'
            f'<span class="sr">{word}</span></b><span>{e(pct_txt)}</span><small>{e(ev)}</small></span></div></li>')
    shown = (f'<p class="muted small">Showing {len(rows)} of {s["series"]:,} versions'
             + (", the top 200 for this sort" if len(rows) >= 200 else "") + '.</p>')
    note = ('<p class="muted small">Prices in your display currency, each sale converted at its own date. Typical sold is '
            'sold evidence only; asks come from the newest nightly snapshot and never move it. The 1 yr column is '
            'PriceCharting\'s own weekly sold data, fetched by a pinned sweep of this machine '
            '(<code>python -m cardman.pc sweep</code>) and shown in your currency at today\'s rate. The grading strip '
            'uses your cost profile: inbound shipping, the PSA fee, and the selling fees you actually pay.</p>')
    return (head + chips + jump + under_html + grade_html
            + '<h2 class="mksboard" id="board">Every printing we can price</h2>'
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
        blocks.append('<div><h3>' + _slope("rising", 25, True) + '<span class="sr">Rising</span></h3>'
                      '<ul class="mini">' + "".join(li(r) for r in up) + "</ul></div>")
    if down:
        blocks.append('<div><h3>' + _slope("falling", 25, True) + '<span class="sr">Falling</span></h3>'
                      '<ul class="mini">' + "".join(li(r) for r in down) + "</ul></div>")
    if not blocks:
        blocks.append('<p class="empty">No version has enough dated sales for a direction yet.</p>')
    return ('<div class="home-h"><h2>Market movement</h2><a href="/market">Whole market →</a></div>'
            f'<p class="muted small">{s["series"]:,} versions priced from real sales · {s["rising"]:,} rising · '
            f'{s["falling"]:,} falling · {s["n_30"]:,} sales in the last 30 days.</p>'
            f'<div class="move-grid">{"".join(blocks)}</div>')
