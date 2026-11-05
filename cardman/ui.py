"""Local review UI over the evidence / decision store.

usage: python -m cardman.ui [--store data/store/evidence.sqlite] [--port 8765]

Binds to 127.0.0.1 only. Standard library only, server-rendered pages and SVG charts; one small
script (served from this module, no third-party code) shows tooltips. Every value shown comes from
the store and is escaped; source links open only for http(s). Nothing here buys or bids: YES records
that a person authorized the next manual step.
"""
from __future__ import annotations

import argparse
import html
import secrets
import statistics
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote as _quote, unquote, urlencode, urlsplit

from . import charts
from .charts import Point, Ref
from .economics import AcquisitionCosts, ExitCosts
from .evidence import import_manual_csv, select_comps
from .fx import FxRate, FxTable
from .store import ImmutableViolation, Store, replay
from .workflow import Action, Decision

STATUSES = ("CANCELLED", "REFUNDED", "RELISTED", "INVALID", "DUPLICATE", "VALID")
MAX_UPLOAD = 5 * 1024 * 1024
MIN_COMPS = 3
SYN = ' <span class="tag syn">synthetic</span>'

GATES = ("identity", "authenticity", "comps", "ceiling", "fresh_availability")
GATE_LABEL = {"identity": "Exact printing", "authenticity": "Authenticity", "comps": "Enough sales",
              "ceiling": "Ask under ceiling", "fresh_availability": "Listing still live"}
GATE_NEXT = {
    "identity": "Confirm the exact printing (set code, rarity, edition) from the photos, then re-evaluate.",
    "authenticity": "Review the photos for authenticity, set authenticity_ok in the listing, then re-evaluate.",
    "comps": "Add verified sales of this exact printing on the Import page, then re-evaluate.",
    "ceiling": "The ask is above what the sales support: wait for a lower price or offer at or under the ceiling.",
    "fresh_availability": "Check the listing is still up, update last_seen, then re-evaluate.",
}
SENS_LABEL = {"exit_minus_10pct": "Resale 10% lower", "lost_combined_shipping_x2": "Inbound shipping doubles",
              "platform_fee_plus_3pt": "Selling fee +3 points", "holding_plus_3_months": "Held 3 months longer"}


# -- formatting ---------------------------------------------------------------------------------

def quote(v) -> str:
    return _quote(str(v), safe="")


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def money(v, cur: str = "") -> str:
    if v in (None, ""):
        return "—"
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return e(v)
    return e(charts.fmt(float(d), cur))


def when(v) -> str:
    if not v:
        return "—"
    dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return e(dt.astimezone().strftime("%Y-%m-%d %H:%M"))


def day(v) -> str:
    dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return e(dt.astimezone().strftime("%Y-%m-%d"))


def link(url: str | None, text: str | None = None) -> str:
    if url and urlsplit(url).scheme in ("http", "https"):
        return f'<a href="{e(url)}" rel="noreferrer noopener" target="_blank">{e(text or "source")}</a>'
    return f'<span class="muted">{e(url or "—")}</span>'


def stage_tag(stage: str | None) -> str:
    return f'<span class="tag {e((stage or "").lower())}">{e(stage or "—")}</span>'


def pct(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def icon(ok: bool | None) -> str:
    if ok is None:
        return '<span class="ic na" aria-label="not applicable">–</span>'
    return ('<span class="ic ok" aria-label="pass">✓</span>' if ok
            else '<span class="ic no" aria-label="fail">✕</span>')


CSS = """
:root{color-scheme:light;--bg:#f9f9f7;--surface:#fcfcfb;--fg:#0b0b0b;--fg2:#52514e;--muted:#898781;
--line:#e1e0d9;--axis:#c3c2b7;--soft:#f0efec;--accent:#256abf;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--neg:#e34948;
--good:#0ca30c;--good-text:#006300;--crit:#d03b3b;--warn:#fab219}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#0d0d0d;
--surface:#1a1a19;--fg:#f0efec;--fg2:#c3c2b7;--muted:#898781;--line:#2c2c2a;--axis:#383835;--soft:#232322;
--accent:#6da7ec;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--neg:#e66767;--good-text:#0ca30c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",
"Noto Sans TC","Noto Sans JP",sans-serif}
header{background:var(--surface);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:18px;
align-items:baseline;flex-wrap:wrap}
header b{font-size:15px}header a{color:var(--fg2);text-decoration:none}header a.on{color:var(--fg);
font-weight:600}header .store{margin-left:auto;color:var(--muted);font-size:12px}
main{max-width:1120px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:6px 0 2px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h2{font-size:15px;margin:0 0 4px}
.sub{color:var(--fg2);margin:0 0 14px}
section{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:16px;margin:0 0 14px}
section .why{color:var(--fg2);margin:0 0 10px;max-width:78ch}
a{color:var(--accent)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--fg2);font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td.d{white-space:nowrap}
table.compact td{padding:4px 8px}
.wrap{overflow-x:auto}
.muted{color:var(--muted)}.fg2{color:var(--fg2)}.good{color:var(--good-text)}.bad{color:var(--crit)}
.tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:12px;font-weight:600;border:1px solid var(--axis);
color:var(--fg2)}
.tag.qualified{color:var(--good-text);border-color:var(--good)}.tag.spectate{color:var(--fg2)}
.tag.syn{font-weight:400;border-style:solid;color:var(--muted)}
.ic{display:inline-flex;width:18px;height:18px;border-radius:50%;align-items:center;justify-content:center;
font-size:11px;font-weight:700;color:#fff;flex:none}
.ic.ok{background:var(--good)}.ic.no{background:var(--crit)}.ic.na{background:var(--axis);color:var(--fg)}
.verdict .headline{font-size:22px;line-height:1.3;margin:0 0 4px}
.verdict .subline{color:var(--fg2);margin:0 0 14px}
.gates{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;margin:0;padding:0;
list-style:none}
.gates li{display:flex;gap:8px;align-items:flex-start;padding:8px;border:1px solid var(--line);border-radius:6px}
.gates b{display:block;font-size:13px}.gates small{color:var(--fg2);display:block;line-height:1.35}
.next{margin:12px 0 0;padding:10px 12px;background:var(--soft);border-radius:6px}
.next ol{margin:4px 0 0;padding-left:20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 14px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.tile small{display:block;color:var(--fg2);font-size:12px}.tile span{font-size:22px;font-weight:600}
.tile em{display:block;font-style:normal;color:var(--muted);font-size:12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:860px){.cols{grid-template-columns:1fr}}
.cols>section{margin:0 0 14px;min-width:0}
svg.chart{width:100%;height:auto;display:block;overflow:visible}
.chartbox{overflow-x:auto}
@media(max-width:600px){table.compact td:last-child{display:none}main{padding:12px}section{padding:12px}}
svg text{font:12px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--fg2)}
svg text.tick{fill:var(--muted);font-variant-numeric:tabular-nums}
svg text.strong{fill:var(--fg);font-weight:600}svg text.rl{paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round}svg text.inbar{fill:#fff;font-weight:600;stroke:none}
td code{white-space:nowrap}
.grid{stroke:var(--line);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.leader{stroke:var(--axis);fill:none}
.ref-ask{stroke:var(--s2);stroke-width:2}.ref-ceiling{stroke:var(--fg);stroke-width:2}
.ref-exit{stroke:var(--muted);stroke-width:1}
.pt{stroke:var(--surface);stroke-width:2}.pt.unused{fill:var(--surface);stroke-width:2}
.pt.void{fill:var(--surface);stroke:var(--muted);stroke-width:1.5}.void-x{stroke:var(--muted);stroke-width:1.5}
.hit{stroke:transparent;stroke-width:14;fill:transparent}.hit-r{fill:transparent}
[data-tip]{outline:none;cursor:default}[data-tip]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
g[data-tip]:hover .pt{stroke:var(--fg)}g[data-tip]:hover path{opacity:.85}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0 0 8px;color:var(--fg2);font-size:12px}
.legend span{display:inline-flex;gap:6px;align-items:center}
.split{position:relative;margin:8px 0 34px}
.split .bar{display:flex;gap:2px;height:28px}
.split .seg{border-radius:4px;color:#fff;font-size:12px;font-weight:600;display:flex;align-items:center;
padding:0 6px;min-width:2px;overflow:hidden;white-space:nowrap}
.split .seg:nth-child(4){color:#0b0b0b}
.split .marker{position:absolute;top:-4px;bottom:-4px}
.split .marker b{position:absolute;left:-1px;top:0;bottom:0;width:2px;background:var(--fg)}
.split .marker span{position:absolute;top:40px;left:-1px;white-space:nowrap;font-size:12px;font-weight:600}
.split .marker span.right{left:auto;right:-1px}
.key{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.track{fill:var(--line)}.need{stroke:var(--fg);stroke-width:1.5}
.room{display:inline-flex;gap:6px;align-items:center;font-variant-numeric:tabular-nums}
.tip{position:fixed;z-index:10;display:none;pointer-events:none;background:var(--surface);color:var(--fg2);
border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;max-width:320px;
box-shadow:0 4px 14px rgba(0,0,0,.12)}
.tip .tv{color:var(--fg);font-weight:600;font-size:13px}
.note{padding:8px 12px;border-left:3px solid var(--axis);background:var(--soft);margin:0 0 12px;border-radius:0 6px 6px 0}
.note.bad{border-color:var(--crit)}.note.ok{border-color:var(--good)}
form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin:8px 0}
label{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--fg2)}
input,select,textarea,button{font:inherit;color:var(--fg);background:var(--surface);border:1px solid var(--axis);
border-radius:6px;padding:5px 8px}
textarea{width:100%;min-height:60px}
button{cursor:pointer;background:var(--soft)}button:hover{border-color:var(--fg2)}
details summary{cursor:pointer;color:var(--fg2)}
ul{margin:4px 0;padding-left:18px}
code{font-size:12px}
"""

JS = r"""
(function(){var tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
function show(el,x,y){var t=el.getAttribute('data-tip');if(!t)return;tip.textContent='';
t.split('\n').forEach(function(l,i){var d=document.createElement('div');d.textContent=l;if(i===0)d.className='tv';
tip.appendChild(d)});tip.style.display='block';var r=tip.getBoundingClientRect(),L=x+14,T=y+14;
if(L+r.width>innerWidth-8)L=x-r.width-14;if(T+r.height>innerHeight-8)T=y-r.height-14;
tip.style.left=Math.max(4,L)+'px';tip.style.top=Math.max(4,T)+'px'}
document.addEventListener('pointermove',function(ev){var el=ev.target.closest&&ev.target.closest('[data-tip]');
if(el)show(el,ev.clientX,ev.clientY);else tip.style.display='none'});
document.addEventListener('focusin',function(ev){var el=ev.target.closest&&ev.target.closest('[data-tip]');
if(el){var r=el.getBoundingClientRect();show(el,r.right,r.top)}});
document.addEventListener('focusout',function(){tip.style.display='none'});})();
"""


def page(title: str, body: str, *, nav: str, store: str, msg: str = "", err: str = "") -> bytes:
    tabs = "".join(f'<a href="{h}" class="{"on" if k == nav else ""}">{k.title()}</a>'
                   for k, h in (("deals", "/"), ("evidence", "/evidence"), ("import", "/import")))
    flash = (f'<div class="note ok">{e(msg)}</div>' if msg else "") + (f'<div class="note bad">{e(err)}</div>' if err else "")
    tz = datetime.now().astimezone().strftime("UTC%z")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · CardMan</title>'
            f'<style>{CSS}</style></head><body><header><b>CardMan</b>{tabs}'
            f'<span class="store">{e(store)} · times {e(tz)}</span></header>'
            f'<main>{flash}{body}</main><script src="/static/ui.js"></script></body></html>').encode()


# -- deal model: everything a deal page or row needs, derived from one stored snapshot -----------

def _d(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def deal_model(st: Store, sid: str, now: datetime) -> dict:
    meta = st.snapshot_meta(sid)
    snap = st.snapshot(sid)
    rep, lst = snap.get("report", {}), snap.get("listing", {})
    home = lst.get("home_currency", "TWD")
    created = datetime.fromisoformat(meta["created_at"])
    fx = FxTable([FxRate(r["base"], r["quote"], Decimal(str(r["mid"])), datetime.fromisoformat(r["as_of"]),
                         r["source"], Decimal(str(r.get("spread_pct", "0")))) for r in lst.get("fx", [])])
    ask = rep.get("ask") or lst.get("ask") or {}
    ask_home = _d(ask.get("home"))
    if ask_home is None and ask:
        try:
            ask_home = fx.convert(Decimal(str(ask["amount"])), ask["currency"], home, created)
        except ValueError:
            ask_home = None
    bc = rep.get("buy_ceiling", {})
    ceiling = _d(bc.get("ceiling"))
    sc = rep.get("exit_scenarios", {})
    scen = {k: _d(sc.get(k)) for k in ("conservative", "base", "upside")}
    room = float((ceiling - ask_home) / ceiling * 100) if ceiling and ask_home is not None else None

    # gates: stored in full since this version; older snapshots only kept the failures
    if rep.get("gates"):
        gates = rep["gates"]
    else:
        failed = {b.split(":")[0]: b.split(":", 1)[1].strip() for b in rep.get("status", {}).get("blocked_by", [])}
        gates = [{"name": g, "passed": g not in failed, "detail": failed.get(g, "ok")} for g in GATES]

    # economics at the ask, per exit scenario
    ci = (snap.get("valuation_inputs") or {}).get("ceiling_inputs")
    at_ask, split, required = [], None, None
    if ci:
        acq, ex = AcquisitionCosts(**ci["acquisition"]), ExitCosts(**ci["exit"])
        required = ci["target_return"] + ci["capital_cost_monthly"] * ci["holding_months"]
        if ask_home is not None:
            allin = acq.all_in(ask_home)
            for k in ("conservative", "base", "upside"):
                g = scen[k]
                if g is not None:
                    net = ex.net(g)
                    at_ask.append({"scenario": k, "gross": g, "net": net, "profit": net - allin,
                                   "ret": (net - allin) / allin if allin else None, "allin": allin})
        if ceiling is not None and scen["conservative"] is not None:
            g = scen["conservative"]
            net = ex.net(g)
            allin_c = acq.all_in(ceiling)
            split = {"gross": g, "item": ceiling, "acq": allin_c - ceiling, "ret": net - allin_c, "exit": g - net}

    # evidence points: comps as used, the rest as unused / void, converted with the listing's FX
    points, unplotted = [], 0
    comps = rep.get("comps", [])
    for c in comps:
        points.append(Point(datetime.fromisoformat(c["date"]), float(c["home"]), c["type"], "used",
                            f"{charts.fmt(float(c['home']), home)}\n{c['type'].replace('_', ' ').lower()} · "
                            f"{c['source']} · {day(c['date'])}\n{c['amount']} {c['currency']} · used as comp"
                            + (" · synthetic" if c.get("synthetic") else "")))
    rejected = []
    for oid, reason in rep.get("rejected_evidence", []):
        try:
            o = st.effective(oid, created)
        except KeyError:
            rejected.append({"id": oid, "reason": reason, "obs": None})
            continue
        rejected.append({"id": oid, "reason": reason, "obs": o})
        try:
            v = fx.convert(o.original_amount, o.currency, home, o.event_time)
        except ValueError:
            unplotted += 1
            continue
        state = "void" if o.status != "VALID" else "unused"
        points.append(Point(o.event_time, float(v), o.evidence_type.value, state,
                            f"{charts.fmt(float(v), home)}\n{o.evidence_type.value.replace('_', ' ').lower()} · "
                            f"{o.source} · {day(o.event_time)}\n{o.original_amount} {o.currency} · not used: {reason}"))
    refs = []
    if ask_home is not None:
        refs.append(Ref("Ask", float(ask_home), "ask"))
    if ceiling is not None:
        refs.append(Ref("Buy ceiling", float(ceiling), "ceiling"))
    names = {"conservative": "Conservative exit", "base": "Base exit", "upside": "Upside exit"}
    seen: dict[float, list[str]] = {}
    for k in ("conservative", "base", "upside"):
        if scen[k] is not None:
            seen.setdefault(float(scen[k]), []).append(names[k].split()[0])
    for v, ks in seen.items():
        refs.append(Ref(" & ".join(ks) + " exit", v, "exit"))

    for g in gates:
        g["detail"] = gate_detail(g, rep, lst, home, ask_home, ceiling, created)
    expired = bool(meta["expires_at"]) and datetime.fromisoformat(meta["expires_at"]) <= now
    ok, why = st.snapshot_validity(sid)
    return {"sid": sid, "meta": meta, "rep": rep, "listing": lst, "home": home, "created": created,
            "ask": ask, "ask_home": ask_home, "ceiling": ceiling, "scen": scen, "room": room, "gates": gates,
            "at_ask": at_ask, "split": split, "required": required, "points": points, "refs": refs,
            "unplotted": unplotted, "comps": comps, "rejected": rejected, "expired": expired,
            "valid": ok, "invalid_why": why, "bc": bc,
            "synthetic": any(c.get("synthetic") for c in comps),
            "printing": (rep.get("identity", {}).get("candidates") or [None])[0]
            if rep.get("identity", {}).get("status") == "RESOLVED" else None}


def gate_detail(g: dict, rep: dict, lst: dict, home: str, ask_home, ceiling, created: datetime) -> str:
    name = g["name"]
    if name == "identity":
        ident = rep.get("identity", {})
        if ident.get("status") == "RESOLVED":
            return "one printing matches"
        n = len(ident.get("candidates", []))
        return f"{ident.get('status', '').lower()}: {n} printings still possible" if n else "no printing matched"
    if name == "authenticity":
        v = lst.get("authenticity_ok")
        return "not reviewed yet" if v is None else ("reviewed: ok" if v else "reviewed: failed")
    if name == "comps":
        n = len(rep.get("comps", []))
        return f"{n} usable sale{'s' if n != 1 else ''}, need {MIN_COMPS}"
    if name == "ceiling":
        if ask_home is None:
            return "no ask recorded"
        if ceiling is None:
            return f"ask {charts.fmt(float(ask_home), home)}; no ceiling to compare"
        return f"ask {charts.fmt(float(ask_home), home)} vs ceiling {charts.fmt(float(ceiling), home)}"
    if name == "fresh_availability":
        seen = lst.get("last_seen")
        if not seen:
            return "never confirmed live"
        hrs = (created - datetime.fromisoformat(seen)).total_seconds() / 3600
        return f"last seen {hrs:.0f} h before valuation (limit 6 h)"
    return g.get("detail", "")


def headline(m: dict) -> tuple[str, str]:
    home, ask_home, ceiling = m["home"], m["ask_home"], m["ceiling"]
    cons = next((a for a in m["at_ask"] if a["scenario"] == "conservative"), None)
    if ceiling is None:
        reason = m["bc"].get("reason") or "identity not resolved"
        return ("No buy ceiling yet", f"Why: {reason}. Until there is one, the ask cannot be judged.")
    if ask_home is None:
        return (f"Buy ceiling {charts.fmt(float(ceiling), home)}", "No ask recorded for this listing.")
    diff = float(ceiling - ask_home)
    if diff >= 0:
        h = (f"Ask is {charts.fmt(diff, home)} under the buy ceiling "
             f"<span class=\"good\">({m['room']:.1f}% room)</span>")
    else:
        h = (f"Ask is {charts.fmt(-diff, home)} over the buy ceiling "
             f"<span class=\"bad\">({-m['room']:.1f}% too high)</span>")
    s = ""
    if cons and cons["ret"] is not None and m["required"] is not None:
        s = (f"Bought at the ask and resold at the conservative exit ({charts.fmt(float(cons['gross']), home)}, "
             f"the low end of {len(m['comps'])} sales), it returns {float(cons['ret']) * 100:.1f}% after all costs; "
             f"the target is {float(m['required']) * 100:.1f}% including capital cost.")
    return h, s


# -- views ------------------------------------------------------------------------------------

def gate_icons(gates: list[dict]) -> str:
    return ('<span class="room">' + "".join(
        f'<span data-tip="{e(("Pass" if g["passed"] else "Fails") + chr(10) + GATE_LABEL.get(g["name"], g["name"]) + chr(10) + g["detail"])}" '
        f'tabindex="0">{icon(g["passed"])}</span>' for g in gates) + "</span>")


def view_deals(st: Store, now: datetime) -> str:
    models, broken, seen = [], [], {}
    for s in st.snapshots():
        seen[s["listing_ref"]] = seen.get(s["listing_ref"], 0) + 1
        if seen[s["listing_ref"]] > 1:
            continue
        try:
            m = deal_model(st, s["id"], now)
        except ImmutableViolation:
            broken.append(s["id"])
            continue
        m["decision"] = s["decision"]
        models.append(m)
    if not models and not broken:
        return ('<h1>Deals</h1><p class="sub">No valuations stored yet. Run '
                '<code>python -m cardman.slice listing.json [obs.csv] --store PATH</code> to add one.</p>')
    rank = {"QUALIFIED": 0, "OBSERVE": 1, "SPECTATE": 2}
    models.sort(key=lambda m: (rank.get(m["meta"]["stage"], 3), -(m["room"] if m["room"] is not None else -1e9)))
    n_q = sum(m["meta"]["stage"] == "QUALIFIED" for m in models)
    n_pending = sum(m["decision"] is None and not m["expired"] for m in models)
    n_room = sum(m["room"] is not None and m["room"] >= 0 for m in models)
    older = sum(n - 1 for n in seen.values())
    out = ['<h1>Deals</h1><p class="sub">Latest valuation per listing, qualified first, then by room under the '
           'ceiling.' + (f' {older} earlier valuation(s) are on each listing\'s page.' if older else "") + '</p>',
           '<div class="tiles">'
           f'<div class="tile"><small>Listings</small><span>{len(models)}</span></div>'
           f'<div class="tile"><small>Qualified</small><span>{n_q}</span><em>all five checks pass</em></div>'
           f'<div class="tile"><small>Ask under ceiling</small><span>{n_room}</span><em>price works, other checks may not</em></div>'
           f'<div class="tile"><small>Awaiting your decision</small><span>{n_pending}</span><em>not expired</em></div>'
           '</div>']
    rows = []
    for m in models:
        cons = next((a for a in m["at_ask"] if a["scenario"] == "conservative"), None)
        ret = (f'{float(cons["ret"]) * 100:.0f}%' if cons and cons["ret"] is not None else "—")
        ret_cls = ("good" if cons and m["required"] is not None and cons["ret"] >= m["required"]
                   else ("bad" if cons else "muted"))
        n = len(m["comps"])
        rows.append(
            f'<tr><td><a href="/deal/{e(m["sid"])}">{e(m["meta"]["listing_ref"])}</a>'
            f'{SYN if m["synthetic"] else ""}<br><code class="muted">{e(m["printing"] or "printing not resolved")}</code>'
            + ("" if m["valid"] else '<br><span class="bad">identity invalidated</span>') + '</td>'
            f'<td>{stage_tag(m["meta"]["stage"])}</td><td>{gate_icons(m["gates"])}</td>'
            f'<td class="n">{money(m["ask_home"], m["home"]) if m["ask_home"] is not None else "—"}</td>'
            f'<td class="n">{money(m["ceiling"], m["home"])}</td>'
            f'<td>{charts.room_bar(m["room"])}</td>'
            f'<td class="n {ret_cls}">{ret}</td>'
            f'<td class="n">{n} / {MIN_COMPS}</td>'
            f'<td class="d">{when(m["meta"]["created_at"])}<br><span class="muted">'
            f'{"expired" if m["expired"] else "until " + when(m["meta"]["expires_at"])}</span></td>'
            f'<td>{e(m["decision"] or "pending")}</td></tr>')
    for sid in broken:
        rows.append(f'<tr><td colspan="10" class="bad">{e(sid)}: stored content does not match its id</td></tr>')
    out.append('<section><div class="wrap"><table><tr><th>Listing / printing</th><th>Stage</th>'
               '<th>Checks</th><th class="n">Ask</th><th class="n">Buy ceiling</th><th>Room under ceiling</th>'
               '<th class="n">Return at ask</th><th class="n">Sales</th><th>Valued</th><th>Decision</th></tr>'
               + "".join(rows) + '</table></div>'
               '<p class="muted" style="margin:8px 0 0">Checks, in order: exact printing, authenticity, enough sales, '
               'ask under ceiling, listing still live. Return at ask assumes resale at the conservative exit.</p>'
               '</section>')
    return "".join(out)


def view_deal(st: Store, sid: str, now: datetime, token: str) -> str:
    m = deal_model(st, sid, now)
    meta, rep, home = m["meta"], m["rep"], m["home"]
    rp = replay(st, sid)
    ident = rep.get("identity", {})
    out = [f'<h1>{e(meta["listing_ref"])} {stage_tag(meta["stage"])}{SYN if m["synthetic"] else ""}</h1>',
           f'<p class="sub"><code>{e(m["printing"] or "printing not resolved")}</code> · valued '
           f'{when(meta["created_at"])} · {"expired " if m["expired"] else "valid until "}{when(meta["expires_at"])}'
           f' · {"<span class=good>✓ replays exactly</span>" if rp["matches"] else "<span class=bad>✕ replay mismatch</span>"}'
           '</p>']
    if not m["valid"]:
        out.append('<div class="note bad">Identity invalidated after this valuation: '
                   + e("; ".join(m["invalid_why"])) + '. YES is refused.</div>')
    if m["synthetic"]:
        out.append('<div class="note">The sales behind this valuation are synthetic fixtures, not market evidence.</div>')

    # verdict
    h, s = headline(m)
    failed = [g for g in m["gates"] if not g["passed"]]
    out.append('<section class="verdict">'
               f'<p class="headline">{h}</p><p class="subline">{e(s)}</p><ul class="gates">'
               + "".join(f'<li>{icon(g["passed"])}<div><b>{e(GATE_LABEL.get(g["name"], g["name"]))}</b>'
                         f'<small>{e(g["detail"])}</small></div></li>' for g in m["gates"]) + '</ul>'
               + (('<div class="next"><b>' + ("All five checks pass. Ready for your YES / WAIT / NO."
                                              if not failed else f"{len(failed)} of {len(m['gates'])} checks fail. "
                                              "To move forward:") + '</b>'
                   + ("<ol>" + "".join(f"<li>{e(GATE_NEXT.get(g['name'], g['detail']))}</li>" for g in failed
                                       if not (g["name"] == "ceiling" and m["ceiling"] is None))
                      + "</ol>" if failed else "") + '</div>'))
               + '</section>')

    # tiles
    cons = next((a for a in m["at_ask"] if a["scenario"] == "conservative"), None)
    ask = m["ask"]
    ask_sub = (f'{money(ask.get("amount"))} {e(ask.get("currency"))}' if ask and ask.get("currency") != home else "")
    out.append('<div class="tiles">'
               f'<div class="tile"><small>Ask</small><span>{money(m["ask_home"], home)}</span><em>{ask_sub}</em></div>'
               f'<div class="tile"><small>Buy ceiling</small><span>{money(m["ceiling"], home)}</span>'
               f'<em>most you can pay and still hit the target</em></div>'
               f'<div class="tile"><small>Room under ceiling</small><span>{pct(m["room"], True) if m["room"] is not None else "—"}</span>'
               f'<em>negative = ask too high</em></div>'
               f'<div class="tile"><small>Return at ask</small><span>'
               f'{pct(float(cons["ret"]) * 100) if cons and cons["ret"] is not None else "—"}</span>'
               f'<em>target {pct(float(m["required"]) * 100) if m["required"] is not None else "—"}</em></div>'
               f'<div class="tile"><small>Verified sales</small><span>{len(m["comps"])} / {MIN_COMPS}</span>'
               f'<em>exact printing, same grade</em></div></div>')

    # price chart
    n_used = len(m["comps"])
    out.append('<section><h2>Sales, ask and ceiling</h2>'
               '<p class="why">Every record for this printing, in ' + e(home) + '. Filled marks are the sales the '
               f'valuation used ({n_used}); hollow ones were seen but not used (hover for the reason). The '
               'conservative exit is the low end of the used sales; the buy ceiling is what is left of it after '
               'selling costs, the target return and buying costs. The ask needs to sit at or below the ceiling.</p>'
               + (charts.price_legend(m["points"], m["refs"])
                  + charts.price_chart(m["points"], m["refs"], currency=home, valued_at=m["created"],
                                       label="Sales, ask and buy ceiling over time")
                  if m["points"] else '<p class="note">No records to plot: '
                  + ("the printing is not resolved, so no sales can be matched to it."
                     if not m["printing"] else "no records for this printing yet.") + '</p>')
               + (f'<p class="muted">{m["unplotted"]} record(s) not plotted: no exchange rate for their date in the '
                  'listing file.</p>' if m["unplotted"] else "")
               + '</section>')

    # where the money goes / profit at ask
    left, right = "", ""
    if m["split"]:
        sp = m["split"]
        parts = [("Item price (buy ceiling)", float(sp["item"]), "--s1", "what you can pay the seller"),
                 ("Buying costs", float(sp["acq"]), "--s2", "shipping in, FX spread, proxy, duty"),
                 ("Target return + capital", float(sp["ret"]), "--s3", "the profit the ceiling protects"),
                 ("Selling costs", float(sp["exit"]), "--s4", "platform fee, shipping out, returns reserve")]
        marker = ("Ask", float(m["ask_home"])) if m["ask_home"] is not None else None
        left = ('<section><h2>Where a resale at the conservative exit goes</h2>'
                f'<p class="why">If the card resells for {money(sp["gross"], home)}, this is how that splits when '
                'you pay exactly the ceiling. The black line is the ask on the same scale: left of the first '
                'boundary means you keep more than the target.</p>'
                + charts.split_bar(parts, float(sp["gross"]), home, marker) + '</section>')
    if m["at_ask"]:
        rows = []
        label = {"conservative": "Conservative exit", "base": "Base exit", "upside": "Upside exit"}
        for a in m["at_ask"]:
            rows.append((f'{label[a["scenario"]]} {charts.compact(float(a["gross"]))}', float(a["profit"]),
                         f'{float(a["ret"]) * 100:.0f}% return' if a["ret"] is not None else ""))
        right = ('<section><h2>Profit if you buy at the ask</h2>'
                 f'<p class="why">All-in cost at the ask is {money(m["at_ask"][0]["allin"], home)}. Profit after '
                 'selling costs for each resale scenario; the target return is '
                 f'{pct(float(m["required"]) * 100) if m["required"] is not None else "—"}.</p>'
                 + charts.hbars(rows, home, width=560, label="Profit at the ask by exit scenario",
                                target=("Target", float(m["required"] * m["at_ask"][0]["allin"]))
                                if m["required"] is not None else None)
                 + '<p class="muted">Bars past the black line beat the target; bars short of it make money but '
                 'less than the target; bars left of zero lose money.</p></section>')
    if left or right:
        out.append(f'<div class="cols">{left}{right}</div>')

    sens = {k: v for k, v in (rep.get("sensitivities") or {}).items() if k != "base" and v is not None}
    if sens:
        rows = []
        for k, v in sens.items():
            extra = ""
            if m["ceiling"] is not None:
                new = m["ceiling"] + Decimal(str(v))
                extra = f"ceiling {charts.fmt(float(new))}"
                if m["ask_home"] is not None:
                    extra += " · ask still fits" if m["ask_home"] <= new else " · ask would be over"
            rows.append((SENS_LABEL.get(k, k.replace("_", " ")), float(v), extra))
        rows.sort(key=lambda r: r[1])
        out.append('<section><h2>What moves the ceiling</h2>'
                   f'<p class="why">Change in the buy ceiling ({money(m["ceiling"], home)}) if one assumption is '
                   'wrong. The biggest bar is the assumption to double-check before saying YES.</p>'
                   + charts.hbars(rows, home, width=1060, label="Ceiling sensitivity") + '</section>')

    # decision
    hist = st.decisions(sid)
    opts = '<option value="">Choose</option>' + "".join(
        f'<option value="{a.value}"{" disabled" if a == Action.YES and not m["valid"] else ""}>{a.value}</option>'
        for a in Action)
    out.append('<section><h2>Decision</h2>'
               + ('<table><tr><th>Action</th><th>Rationale</th><th>Wait trigger</th><th>When</th></tr>'
                  + "".join(f"<tr><td>{e(a)}</td><td>{e(r)}</td><td>{e(w or '—')}</td><td>{when(t)}</td></tr>"
                            for a, r, w, t in hist) + "</table>" if hist else "")
               + f'<form class="inline" method="post" action="/deal/{e(sid)}/decide">'
               f'<input type="hidden" name="t" value="{e(token)}">'
               f'<label>Action<select name="action" required>{opts}</select></label>'
               '<label style="flex:1 1 260px">Rationale<input name="rationale" required></label>'
               '<label style="flex:1 1 200px">Wait trigger (for WAIT)<input name="wait_trigger"></label>'
               '<button>Record</button></form>'
               '<p class="muted">YES records your authorization for the next manual step. Nothing is bought or bid.</p>'
               f'<form class="inline" method="post" action="/deal/{e(sid)}/reevaluate">'
               f'<input type="hidden" name="t" value="{e(token)}"><button>Re-evaluate now from the store</button>'
               '<span class="muted">New valuation with the current evidence and status events.</span></form>'
               '</section>')

    # evidence table: same records as the chart
    rows = []
    for c in m["comps"]:
        rows.append(f'<tr><td><span class="ic ok" aria-label="used">✓</span></td><td class="d">{day(c["date"])}</td>'
                    f'<td>{e(c["type"])}</td><td>{e(c["source"])}{SYN if c.get("synthetic") else ""}</td>'
                    f'<td class="n">{money(c["amount"], c["currency"])}</td><td class="n">{money(c["home"])}</td>'
                    f'<td class="fg2">used as comp</td>'
                    f'<td><a href="/obs/{quote(c["id"])}">{e(c["id"])}</a> · {link(c.get("url"))}</td></tr>')
    for r in m["rejected"]:
        o = r["obs"]
        rows.append(f'<tr><td><span class="ic na" aria-label="not used">–</span></td>'
                    f'<td class="d">{day(o.event_time) if o else "—"}</td><td>{e(o.evidence_type.value if o else "")}</td>'
                    f'<td>{e(o.source if o else "")}{SYN if o and o.synthetic_fixture else ""}</td>'
                    f'<td class="n">{money(o.original_amount, o.currency) if o else "—"}</td><td class="n">—</td>'
                    f'<td class="fg2">{e(r["reason"])}</td>'
                    f'<td><a href="/obs/{quote(r["id"])}">{e(r["id"])}</a>'
                    f'{" · " + link(o.source_url) if o else ""}</td></tr>')
    out.append(f'<section><h2>Evidence ({len(m["comps"])} used, {len(m["rejected"])} not used)</h2>'
               '<p class="why">The table behind the chart. A record is only a comp if it is a completed sale of this '
               'exact printing, single copy, same grade, still valid, and dated before the valuation.</p>'
               '<div class="wrap"><table><tr><th></th><th>Date</th><th>Type</th><th>Source</th>'
               f'<th class="n">Amount</th><th class="n">{e(home)}</th><th>Used?</th><th>Record</th></tr>'
               + "".join(rows) + '</table></div>'
               + ('' if rows else '<p class="muted">No records.</p>') + '</section>')

    # identity + history + calculation
    out.append('<div class="cols"><section><h2>Identity</h2>'
               f'<p>{icon(ident.get("status") == "RESOLVED")} {e(ident.get("status"))}</p>'
               + "".join(f"<div><code>{e(c)}</code></div>" for c in ident.get("candidates", []))
               + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in ident.get("evidence", [])) + "</ul>"
                  if ident.get("evidence") else "")
               + "".join(f'<div class="bad">{e(x)}</div>' for x in ident.get("blocking", []))
               + "".join(f'<div class="fg2">needs: {e(x)}</div>' for x in ident.get("evidence_needed", []))
               + '</section>')
    others = [x for x in st.snapshots() if x["listing_ref"] == meta["listing_ref"]]
    hist_rows = []
    for x in others:
        try:
            xm = deal_model(st, x["id"], now)
        except ImmutableViolation:
            continue
        idcell = (f'<code>{e(x["id"][:10])}</code> (this)' if x["id"] == sid
                  else f'<a href="/deal/{e(x["id"])}"><code>{e(x["id"][:10])}</code></a>')
        hist_rows.append(f'<tr><td class="d">{when(x["created_at"])}</td><td>{stage_tag(x["stage"])}</td>'
                         f'<td class="n">{money(xm["ceiling"], home)}</td><td class="n">{len(xm["comps"])}</td>'
                         f'<td>{e(x["decision"] or "pending")}</td><td>{idcell}</td></tr>')
    out.append('<section><h2>Valuations of this listing</h2>'
               '<div class="wrap"><table><tr><th>Valued</th><th>Stage</th><th class="n">Ceiling</th><th class="n">Sales</th>'
               '<th>Decision</th><th>Snapshot</th></tr>' + "".join(hist_rows) + '</table></div></section></div>')
    bc = m["bc"]
    if bc.get("ceiling") is not None:
        out.append('<section><details><summary>Calculation details</summary><table class="compact">'
                   + "".join(f'<tr><td class="fg2">{e(k.replace("_", " "))}</td><td class="n">{e(v)}</td></tr>'
                             for k, v in bc.items() if k != "reason")
                   + f'<tr><td class="fg2">snapshot</td><td class="n"><code>{e(sid)}</code></td></tr>'
                   '</table></details></section>')
    return "".join(out)


def reason_group(r: str) -> str:
    if r == "comp":
        return "Used as comp"
    for key, label in (("is not a sale", "Not a sale (asks, bids, estimates)"),
                       ("status ", "Cancelled / refunded / invalid"), ("synthetic", "Synthetic fixture"),
                       ("identity", "Printing not confirmed"), ("lot", "Lot or several copies"),
                       ("grade", "Different grade"), ("condition", "Different condition"),
                       ("duplicate", "Same sale seen twice"), ("after decision", "After the valuation time")):
        if key in r:
            return label
    return r


def view_evidence(st: Store, now: datetime, printing: str | None, include_synthetic: bool) -> str:
    printings = st.printing_ids()
    opts = '<option value="">All printings</option>' + "".join(
        f'<option{" selected" if p == printing else ""}>{e(p)}</option>' for p in printings)
    out = ['<h1>Evidence</h1><p class="sub">Market records in the store and whether a valuation made now would use '
           'them.</p><form class="inline" method="get" action="/evidence">'
           f'<label>Printing<select name="printing">{opts}</select></label>'
           f'<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="syn" value="1"'
           f'{" checked" if include_synthetic else ""}>count synthetic fixtures</label><button>Show</button></form>']
    if not printings:
        out.append('<section><p class="muted">No observations stored. Use Import to add a manual CSV.</p></section>')
        return "".join(out)

    def verdicts(pid):
        comps, rejected = select_comps(st.observations_for(pid, as_of=now), pid, as_of=now,
                                       include_synthetic=include_synthetic)
        v = {o.id: "comp" for o in comps}
        v.update({i: r for i, r in rejected})
        return comps, v

    if not printing:
        rows = []
        stats = []
        for pid in printings:
            obs = st.observation_list(pid, as_of=now)
            comps, _ = verdicts(pid)
            stats.append((pid, obs, comps))
        scale = max([MIN_COMPS * 2] + [len(c) for _, _, c in stats])
        for pid, obs, comps in sorted(stats, key=lambda s: -len(s[2])):
            cur = {c.currency for c in comps}
            med = (charts.fmt(float(statistics.median(c.original_amount for c in comps)), next(iter(cur)))
                   if comps and len(cur) == 1 else "—")
            last = max((c.event_time for c in comps), default=None)
            rows.append(f'<tr><td><a href="/evidence?{urlencode({"printing": pid, **({"syn": 1} if include_synthetic else {})})}">'
                        f'<code>{e(pid)}</code></a></td><td class="n">{len(obs)}</td>'
                        f'<td><span class="room">{charts.count_bar(len(comps), MIN_COMPS, scale)}'
                        f'<span>{len(comps)}</span></span></td><td class="n">{e(med)}</td>'
                        f'<td class="d">{day(last) if last else "—"}</td></tr>')
        out.append('<section><h2>Coverage by printing</h2><p class="why">How many usable sales each printing has. '
                   f'A valuation needs {MIN_COMPS} (the black tick); fewer means no buy ceiling.</p>'
                   '<div class="wrap"><table><tr><th>Printing</th><th class="n">Records</th><th>Usable sales</th>'
                   '<th class="n">Median sale</th><th>Latest sale</th></tr>' + "".join(rows) + '</table></div></section>')
        return "".join(out)

    obs = st.observation_list(printing, as_of=now)
    comps, verdict = verdicts(printing)
    groups: dict[str, int] = {}
    for o in obs:
        g = reason_group(verdict.get(o.id, "not in pool"))
        groups[g] = groups.get(g, 0) + 1
    need = max(0, MIN_COMPS - len(comps))
    out.append('<div class="tiles">'
               f'<div class="tile"><small>Records</small><span>{len(obs)}</span></div>'
               f'<div class="tile"><small>Usable sales now</small><span>{len(comps)}</span>'
               f'<em>{"enough for a ceiling" if not need else f"{need} more needed"}</em></div>'
               + (f'<div class="tile"><small>Median usable sale</small><span>'
                  f'{e(charts.fmt(float(statistics.median(c.original_amount for c in comps)), comps[0].currency))}'
                  f'</span><em>original currency</em></div>'
                  if comps and len({c.currency for c in comps}) == 1 else "")
               + '</div>')
    grows = sorted(groups.items(), key=lambda kv: (kv[0] != "Used as comp", -kv[1]))
    gmax = max(groups.values())
    out.append('<div class="cols"><section><h2>Why records are or are not used</h2>'
               '<p class="why">Each record counted once, by the first rule it fails.</p><table class="compact">'
               + "".join(f'<tr><td>{e(g)}</td><td><span class="room">{charts.count_bar(n, None, gmax)}'
                         f'<span>{n}</span></span></td></tr>' for g, n in grows)
               + '</table></section><section><h2>Reading the chart</h2><p class="why">Filled marks would be used as '
               'comps right now; hollow marks are seen but not used; crossed marks were cancelled, refunded or '
               'judged invalid. Shapes separate sale types. Amounts are in the currency recorded, one chart per '
               'currency.</p></section></div>')
    by_cur: dict[str, list] = {}
    for o in obs:
        by_cur.setdefault(o.currency, []).append(o)
    for cur, items in by_cur.items():
        pts = []
        for o in items:
            v = verdict.get(o.id, "not in pool")
            state = "used" if v == "comp" else ("void" if o.status != "VALID" else "unused")
            pts.append(Point(o.event_time, float(o.original_amount), o.evidence_type.value, state,
                             f"{charts.fmt(float(o.original_amount), cur)}\n"
                             f"{o.evidence_type.value.replace('_', ' ').lower()} · {o.source} · {day(o.event_time)}\n"
                             + ("used as comp" if v == "comp" else f"not used: {v}")))
        med = statistics.median(float(c.original_amount) for c in comps if c.currency == cur) \
            if any(c.currency == cur for c in comps) else None
        refs = [Ref("Median usable sale", med, "median")] if med is not None else []
        out.append(f'<section><h2>Records over time ({e(cur)})</h2>' + charts.price_legend(pts, refs)
                   + charts.price_chart(pts, refs, currency=cur, valued_at=now, label=f"Records in {cur}")
                   + '</section>')
    rows = []
    for o in obs:
        v = verdict.get(o.id, "not in pool")
        rows.append(
            f'<tr><td>{icon(True) if v == "comp" else icon(None)}</td>'
            f'<td><a href="/obs/{quote(o.id)}">{e(o.id)}</a>{SYN if o.synthetic_fixture else ""}</td>'
            f'<td class="d">{day(o.event_time)}</td><td>{e(o.evidence_type.value)}</td><td>{e(o.source)}</td>'
            f'<td class="n">{money(o.original_amount, o.currency)}</td>'
            f'<td>{e(" ".join(x for x in (o.grade_company, o.grade) if x) or o.observed_condition or "raw")}</td>'
            f'<td class="{"" if o.status == "VALID" else "bad"}">{e(o.status)}</td>'
            f'<td class="fg2">{e("used as comp" if v == "comp" else v)}</td><td>{link(o.source_url)}</td></tr>')
    out.append('<section><h2>All records</h2><div class="wrap"><table><tr><th></th><th>Record</th><th>Event</th>'
               '<th>Type</th><th>Source</th><th class="n">Amount</th><th>Grade / cond.</th><th>Status</th>'
               f'<th>Used?</th><th></th></tr>{"".join(rows)}</table></div></section>')
    return "".join(out)


def view_obs(st: Store, obs_id: str, now: datetime, token: str) -> str:
    orig = st.original(obs_id)
    eff = st.effective(obs_id, now)
    hist = st.status_history(obs_id)
    uses = []
    for pid in orig.printing_candidates:
        comps, rejected = select_comps(st.observations_for(pid, as_of=now), pid, as_of=now, include_synthetic=False)
        r = "used as comp" if any(c.id == obs_id for c in comps) else next((w for i, w in rejected if i == obs_id), "—")
        uses.append(f'<li><code>{e(pid)}</code>: {e(r)}</li>')
    fields_ = [("Type", orig.evidence_type.value), ("Source", orig.source),
               ("Amount", f"{orig.original_amount} {orig.currency}"),
               ("Shipping", orig.shipping_amount), ("Event time", when(orig.event_time)),
               ("Captured", when(orig.capture_time)), ("Printing", ", ".join(orig.printing_candidates)),
               ("Listing id", orig.listing_id), ("Source event id", orig.source_event_id),
               ("Seller", orig.seller_ref), ("Grade", " ".join(x for x in (orig.grade_company, orig.grade) if x)),
               ("Cert", orig.cert_number), ("Condition", orig.observed_condition),
               ("Quantity / lot", f"{orig.quantity}{' · lot' if orig.is_lot else ''}"),
               ("Upstream", ", ".join(orig.upstream_lineage)), ("Raw ref", orig.raw_ref),
               ("Synthetic", "yes" if orig.synthetic_fixture else "no")]
    out = [f'<h1>{e(obs_id)} <span class="tag">{e(eff.status)}</span>{SYN if orig.synthetic_fixture else ""}</h1>',
           f'<p class="sub">{e(orig.evidence_type.value)} · {money(orig.original_amount, orig.currency)} · '
           f'{when(orig.event_time)} · {link(orig.source_url, orig.source_url)}</p>',
           f'<section><h2>In a valuation made now</h2><ul>{"".join(uses)}</ul></section>',
           '<div class="cols"><section><h2>Source record</h2><p class="why">As captured. It never changes; later '
           'findings are status events.</p><table class="compact">'
           + "".join(f'<tr><td class="fg2">{e(k)}</td><td>{v if k in ("Event time", "Captured") else e(v or "—")}</td></tr>'
                     for k, v in fields_)
           + '</table></section><section><h2>Status history</h2>']
    if hist:
        out.append('<table><tr><th>Status</th><th>Reason</th><th>By</th><th>When</th></tr>'
                   + "".join(f"<tr><td>{e(s)}</td><td>{e(r)}</td><td>{e(a)}</td><td>{when(t)}</td></tr>"
                             for s, r, a, t in hist) + "</table>")
    else:
        out.append('<p class="muted">No status events. Recorded as VALID.</p>')
    opts = '<option value="">Choose</option>' + "".join(f"<option>{s}</option>" for s in STATUSES)
    out.append(f'<form method="post" action="/obs/{quote(obs_id)}/status">'
               f'<input type="hidden" name="t" value="{e(token)}">'
               f'<p><label>New status<select name="status" required>{opts}</select></label></p>'
               '<label>Reason<textarea name="reason" required></textarea></label>'
               '<p><button>Record status</button></p></form>'
               '<p class="muted">Valuations from now on see this; earlier valuations keep what was known then.</p>'
               '</section></div>')
    return "".join(out)


def view_import(token: str) -> str:
    return ('<h1>Import</h1><p class="sub">Manual CSV, same columns as '
            '<code>examples/manual_import_TEMPLATE.csv</code>.</p><section>'
            f'<form method="post" action="/import" enctype="multipart/form-data">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            '<p><input type="file" name="csv" accept=".csv,text/csv" required></p>'
            '<p><button>Import</button></p></form>'
            '<ul class="fg2"><li>Rows already stored are skipped.</li>'
            '<li>A row whose source facts changed is refused and listed; record a status instead.</li>'
            '<li>Rows whose source_url is not http(s), or marked synthetic, are stored as synthetic and never used '
            'in real valuations.</li><li>A file that fails to parse imports nothing.</li></ul></section>')



# -- actions ----------------------------------------------------------------------------------

def import_bytes(st: Store, name: str, data: bytes, now: datetime) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / (Path(name).name or "upload.csv")
        p.write_bytes(data)
        rows = import_manual_csv(p, now)
    added, same, refused = 0, 0, []
    for o in rows:
        try:
            if st.add_observation(o, now=now):
                added += 1
            else:
                same += 1
        except ImmutableViolation:
            refused.append(o.id)
    msg = f"{len(rows)} rows: {added} added, {same} already stored"
    if refused:
        msg += f", {len(refused)} refused"
    err = ("Refused (source facts differ from the stored record; record a status instead): "
           + ", ".join(refused)) if refused else ""
    return msg, err


def reevaluate(st: Store, store_path: str, sid: str, now: datetime) -> str:
    from .slice import run
    snap = st.snapshot(sid)
    listing = snap["listing"]
    synthetic = any(c.get("synthetic") for c in snap.get("report", {}).get("comps", []))
    rep = run(listing, None, now, allow_synthetic=synthetic, store_path=store_path)
    return rep["status"]["stored_snapshot_id"]


# -- server -----------------------------------------------------------------------------------

def make_handler(store_path: str, token: str, clock=lambda: datetime.now(timezone.utc)):
    class H(BaseHTTPRequestHandler):
        server_version = "CardMan"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                             "script-src 'self'; form-action 'self'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, to: str, msg: str = "", err: str = ""):
            sep = "&" if "?" in to else "?"
            extra = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
            self.send_response(303)
            self.send_header("Location", to + (sep + extra if extra else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _page(self, nav, title, body, qs):
            self._send(200, page(title, body, nav=nav, store=store_path,
                                 msg=qs.get("msg", [""])[0], err=qs.get("err", [""])[0]))

        def do_GET(self):
            u = urlsplit(self.path)
            qs = parse_qs(u.query)
            parts = [p for p in u.path.split("/") if p]
            st = Store(store_path)
            now = clock()
            if parts == ["static", "ui.js"]:
                st.db.close()
                return self._send(200, JS.encode(), "text/javascript; charset=utf-8")
            try:
                if not parts:
                    self._page("deals", "Deals", view_deals(st, now), qs)
                elif parts[0] == "deal" and len(parts) == 2:
                    self._page("deals", parts[1], view_deal(st, parts[1], now, token), qs)
                elif parts == ["evidence"]:
                    self._page("evidence", "Evidence", view_evidence(st, now, qs.get("printing", [""])[0] or None,
                                                                     qs.get("syn", [""])[0] == "1"), qs)
                elif parts[0] == "obs" and len(parts) == 2:
                    oid = unquote(parts[1])
                    self._page("evidence", oid, view_obs(st, oid, now, token), qs)
                elif parts == ["import"]:
                    self._page("import", "Import", view_import(token), qs)
                else:
                    self._send(404, page("Not found", "<h1>Not found</h1>", nav="", store=store_path))
            except KeyError as x:
                self._send(404, page("Not found", f"<h1>Not found</h1><p>{e(x)}</p>", nav="", store=store_path))
            finally:
                st.db.close()

        def do_POST(self):
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                return self._send(413, page("Too large", "<h1>Upload too large</h1>", nav="", store=store_path))
            raw = self.rfile.read(n)
            ctype = self.headers.get("Content-Type", "")
            files: dict[str, tuple[str, bytes]] = {}
            if ctype.startswith("multipart/form-data"):
                msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
                form = {}
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if part.get_filename() is not None:
                        files[name] = (part.get_filename(), part.get_payload(decode=True) or b"")
                    else:
                        form[name] = (part.get_payload(decode=True) or b"").decode()
            else:
                form = {k: v[0] for k, v in parse_qs(raw.decode(), keep_blank_values=True).items()}
            if not secrets.compare_digest(form.get("t", ""), token):
                return self._send(403, page("Refused", "<h1>Refused</h1><p>Form token mismatch. Reload the page.</p>",
                                            nav="", store=store_path))
            st = Store(store_path)
            now = clock()
            try:
                if len(parts) == 3 and parts[0] == "deal" and parts[2] == "decide":
                    sid = parts[1]
                    try:
                        st.record_decision(Decision(sid, Action(form.get("action", "")), form.get("rationale", "").strip(),
                                                    now, form.get("wait_trigger", "").strip() or None))
                        self._redirect(f"/deal/{sid}", msg=f"Recorded {form.get('action')}.")
                    except (ValueError, KeyError) as x:
                        self._redirect(f"/deal/{sid}", err=str(x))
                elif len(parts) == 3 and parts[0] == "deal" and parts[2] == "reevaluate":
                    try:
                        new = reevaluate(st, store_path, parts[1], now)
                        self._redirect(f"/deal/{new}", msg="Re-evaluated." if new != parts[1]
                                       else "Re-evaluated: nothing changed, same snapshot.")
                    except (Exception, SystemExit) as x:   # stale FX, missing catalog, ...
                        self._redirect(f"/deal/{parts[1]}", err=f"Re-evaluation failed: {x}")
                elif len(parts) == 3 and parts[0] == "obs" and parts[2] == "status":
                    oid = unquote(parts[1])
                    status = form.get("status", "")
                    if status not in STATUSES:
                        return self._redirect(f"/obs/{quote(oid)}", err=f"unknown status {status!r}")
                    try:
                        st.set_status(oid, status, form.get("reason", "").strip(), actor="ui", now=now)
                        self._redirect(f"/obs/{quote(oid)}", msg=f"Recorded {status}.")
                    except (ValueError, KeyError) as x:
                        self._redirect(f"/obs/{quote(oid)}", err=str(x))
                elif parts == ["import"]:
                    if "csv" not in files:
                        return self._redirect("/import", err="choose a CSV file")
                    try:
                        msg, err = import_bytes(st, *files["csv"], now)
                        self._redirect("/evidence", msg=msg, err=err)
                    except (ValueError, KeyError) as x:
                        self._redirect("/import", err=f"Import failed, nothing was imported: {x}")
                else:
                    self._send(404, page("Not found", "<h1>Not found</h1>", nav="", store=store_path))
            finally:
                st.db.close()

    return H


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m cardman.ui")
    ap.add_argument("--store", default="data/store/evidence.sqlite")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    Store(a.store).db.close()          # create the file and schema up front
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.store, secrets.token_urlsafe(24)))
    print(f"CardMan UI on http://127.0.0.1:{a.port}  (store {a.store}; Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
