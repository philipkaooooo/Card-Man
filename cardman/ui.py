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
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote as _quote, unquote, urlencode, urlsplit

from . import charts
from .advice import advise
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
    return (f"{v:+.1f}%" if signed else f"{v:.1f}%").replace("-", "−")


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
main{max-width:1240px;margin:0 auto;padding:16px}
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
header .curs{display:inline-flex;border:1px solid var(--axis);border-radius:6px;overflow:hidden}
header .curs a{padding:0 8px;font-size:12px}header .curs a.on{background:var(--fg);color:var(--surface)}
.call{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:13px;padding:2px 8px 2px 4px;
border-radius:999px;border:1px solid var(--axis);white-space:nowrap}
.call .ci{display:inline-flex;width:18px;height:18px;border-radius:50%;align-items:center;justify-content:center;
font-size:11px;color:#fff;background:var(--muted)}
.call.good .ci{background:var(--good)}.call.warn .ci{background:var(--warn);color:#0b0b0b}.call.crit .ci{background:var(--crit)}
.call.big{font-size:15px;padding:4px 12px 4px 6px}.call.big .ci{width:22px;height:22px;font-size:13px}
.callcard{border-left-width:6px}.callcard.good{border-left-color:var(--good)}.callcard.warn{border-left-color:var(--warn)}
.callcard.crit{border-left-color:var(--crit)}
.callcard .headline{font-size:26px;line-height:1.25;margin:10px 0 8px;font-weight:600}
.because{margin:0 0 6px;padding-left:20px;font-size:15px}.because li{margin:3px 0}
.conf{color:var(--fg2);margin:10px 0 0;font-size:13px}
.flags{list-style:none;padding:0;margin:0 0 10px}.flags li{display:flex;gap:8px;margin:0 0 8px;align-items:flex-start}
.ic.warnic{background:var(--warn);color:#0b0b0b}
.zones{margin-top:22px}.zones .seg{color:#fff}.zones .seg:nth-child(2){color:#0b0b0b}
.zticks{position:absolute;top:-20px;left:0;right:0;height:16px;font-size:12px;color:var(--fg2)}
.zticks span{position:absolute;transform:translateX(-50%);white-space:nowrap}
table.math tr.total td{font-weight:600;border-top:1px solid var(--axis)}
tr.expired td{opacity:.6}.warnt{color:#9a6700}
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


CURRENCIES = ("USD", "TWD")


def page(title: str, body: str, *, nav: str, store: str, msg: str = "", err: str = "", cur: str = "USD",
         path: str = "/") -> bytes:
    tabs = "".join(f'<a href="{h}" class="{"on" if k == nav else ""}">{k.title()}</a>'
                   for k, h in (("deals", "/"), ("evidence", "/evidence"), ("import", "/import")))
    tabs += ('<span class="curs">' + "".join(
        f'<a href="/currency?{urlencode({"c": c, "back": path})}" class="{"on" if c == cur else ""}">{c}</a>'
        for c in CURRENCIES) + '</span>')
    flash = (f'<div class="note ok">{e(msg)}</div>' if msg else "") + (f'<div class="note bad">{e(err)}</div>' if err else "")
    tz = datetime.now().astimezone().strftime("UTC%z")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · CardMan</title>'
            f'<style>{CSS}</style></head><body><header><b>CardMan</b>{tabs}'
            f'<span class="store">{e(store)} · times {e(tz)}</span></header>'
            f'<main>{flash}{body}</main><script src="/static/ui.js"></script></body></html>').encode()


# -- display currency ---------------------------------------------------------------------------

class Disp:
    """Converts home-currency Decimals to the display currency with the listing's own FX at valuation time.

    Rounding is chosen per number so the display never flatters a deal: prices you may pay round down,
    resale thresholds you need to reach round up.
    """

    def __init__(self, want: str, home: str, fx: FxTable, at: datetime):
        self.home, self.cur, self.factor, self.note = home, home, Decimal(1), ""
        if want and want != home:
            try:
                r = fx.rate(want, home, at)              # home units per 1 display unit
                self.cur, self.factor = want, 1 / r.mid
            except ValueError:
                try:
                    r = fx.rate(home, want, at)
                    self.cur, self.factor = want, r.mid
                except ValueError:
                    self.note = f"No {want}/{home} rate at the valuation time, so amounts are in {home}."

    def v(self, x) -> Decimal | None:
        return None if x is None else Decimal(str(x)) * self.factor

    def f(self, x) -> float | None:
        v = self.v(x)
        return None if v is None else float(v)

    def __call__(self, x, rnd: str = "near") -> str:
        v = self.v(x)
        if v is None:
            return "—"
        if rnd != "near":
            step = Decimal("1") if abs(v) >= 100 or self.cur in ("TWD", "JPY") else Decimal("0.01")
            v = v.quantize(step, rounding=ROUND_FLOOR if rnd == "down" else ROUND_CEILING)
        return e(charts.fmt(float(v), self.cur))


# -- deal model: everything a deal page or row needs, derived from one stored snapshot -----------

def _d(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def deal_model(st: Store, sid: str, now: datetime, want: str = "USD") -> dict:
    meta = st.snapshot_meta(sid)
    snap = st.snapshot(sid)
    rep, lst = snap.get("report", {}), snap.get("listing", {})
    home = lst.get("home_currency", "TWD")
    created = datetime.fromisoformat(meta["created_at"])
    fx = FxTable([FxRate(r["base"], r["quote"], Decimal(str(r["mid"])), datetime.fromisoformat(r["as_of"]),
                         r["source"], Decimal(str(r.get("spread_pct", "0")))) for r in lst.get("fx", [])])
    D = Disp(want, home, fx, created)
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

    if rep.get("gates"):
        gates = [dict(g) for g in rep["gates"]]
    else:   # older snapshots kept only the failures
        failed = {b.split(":")[0]: b.split(":", 1)[1].strip() for b in rep.get("status", {}).get("blocked_by", [])}
        gates = [{"name": g, "passed": g not in failed, "detail": failed.get(g, "ok")} for g in GATES]
    for g in gates:
        g["detail"] = gate_detail(g, rep, lst, D, ask_home, ceiling, created)

    comps = rep.get("comps", [])
    comp_rows = [{"date": datetime.fromisoformat(c["date"]), "home": Decimal(str(c["home"])), "type": c["type"],
                  "source": c["source"], "synthetic": bool(c.get("synthetic"))} for c in comps]
    ci = (snap.get("valuation_inputs") or {}).get("ceiling_inputs")
    ident = rep.get("identity", {})
    adv = advise(ci=ci, ask=ask_home, ceiling=ceiling, scen=scen, comps=comp_rows, valued_at=created, gates=gates,
                 identity_status=ident.get("status"), money=lambda x, rnd="near": D(x, rnd), gate_todo=GATE_NEXT)

    # evidence points in the display currency
    points, unplotted, rejected = [], 0, []
    for c in comps:
        v = D.f(c["home"])
        points.append(Point(datetime.fromisoformat(c["date"]), v, c["type"], "used",
                            f"{charts.fmt(v, D.cur)}\n{c['type'].replace('_', ' ').lower()} · {c['source']} · "
                            f"{day(c['date'])}\n{c['amount']} {c['currency']} · used as comp"
                            + (" · synthetic" if c.get("synthetic") else "")))
    for oid, reason in rep.get("rejected_evidence", []):
        try:
            o = st.effective(oid, created)
        except KeyError:
            rejected.append({"id": oid, "reason": reason, "obs": None})
            continue
        rejected.append({"id": oid, "reason": reason, "obs": o})
        try:
            v = D.f(fx.convert(o.original_amount, o.currency, home, o.event_time))
        except ValueError:
            unplotted += 1
            continue
        points.append(Point(o.event_time, v, o.evidence_type.value, "void" if o.status != "VALID" else "unused",
                            f"{charts.fmt(v, D.cur)}\n{o.evidence_type.value.replace('_', ' ').lower()} · {o.source}"
                            f" · {day(o.event_time)}\n{o.original_amount} {o.currency} · not used: {reason}"))
    refs = []
    if ask_home is not None:
        refs.append(Ref("Ask", D.f(ask_home), "ask"))
    if ceiling is not None:
        refs.append(Ref("Pay up to", D.f(ceiling), "ceiling"))
    seen: dict[float, list[str]] = {}
    for k in ("conservative", "base", "upside"):
        if scen[k] is not None:
            seen.setdefault(D.f(scen[k]), []).append({"conservative": "Low-end", "base": "Typical",
                                                      "upside": "High"}[k])
    for v, ks in seen.items():
        refs.append(Ref(" & ".join(ks) + " resale", v, "exit"))

    expired = bool(meta["expires_at"]) and datetime.fromisoformat(meta["expires_at"]) <= now
    ok, why = st.snapshot_validity(sid)
    return {"sid": sid, "meta": meta, "rep": rep, "listing": lst, "home": home, "D": D, "created": created,
            "ask": ask, "ask_home": ask_home, "ceiling": ceiling, "scen": scen, "room": room, "gates": gates,
            "adv": adv, "ci": ci, "points": points, "refs": refs, "unplotted": unplotted, "comps": comps,
            "rejected": rejected, "expired": expired, "valid": ok, "invalid_why": why, "bc": bc,
            "synthetic": any(c.get("synthetic") for c in comps),
            "printing": (ident.get("candidates") or [None])[0] if ident.get("status") == "RESOLVED" else None}


def gate_detail(g: dict, rep: dict, lst: dict, D: "Disp", ask_home, ceiling, created: datetime) -> str:
    name = g["name"]
    if name == "identity":
        ident = rep.get("identity", {})
        if ident.get("status") == "RESOLVED":
            return "one printing matches"
        n = len(ident.get("candidates", []))
        return f"{n} printings still possible" if n else "no printing matched"
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
            return f"ask {html.unescape(D(ask_home))}; nothing to compare yet"
        return f"ask {html.unescape(D(ask_home))} vs pay-up-to {html.unescape(D(ceiling, 'down'))}"
    if name == "fresh_availability":
        seen = lst.get("last_seen")
        if not seen:
            return "never confirmed live"
        hrs = (created - datetime.fromisoformat(seen)).total_seconds() / 3600
        return f"last seen {hrs:.0f} h before valuation (limit 6 h)"
    return g.get("detail", "")


# -- views ------------------------------------------------------------------------------------

CALL_STYLE = {"BUY": ("good", "✓"), "CHECK": ("good", "✓"), "OFFER": ("warn", "!"), "SKIP": ("crit", "✕"),
              "HOLD": ("muted", "…"), "NONE": ("muted", "–")}
CALL_SHORT = {"BUY": "Buy", "CHECK": "Buy after checks", "OFFER": "Offer lower", "SKIP": "Skip", "HOLD": "Hold off",
              "NONE": "No ask"}


def call_badge(call: str, big: bool = False) -> str:
    cls, ic = CALL_STYLE.get(call, ("muted", "–"))
    return (f'<span class="call {cls}{" big" if big else ""}"><span class="ci">{ic}</span>'
            f'{e(CALL_SHORT.get(call, call))}</span>')


def gate_icons(gates: list[dict]) -> str:
    return ('<span class="room">' + "".join(
        f'<span data-tip="{e(("Pass" if g["passed"] else "Fails") + chr(10) + GATE_LABEL.get(g["name"], g["name"]) + chr(10) + g["detail"])}" '
        f'tabindex="0">{icon(g["passed"])}</span>' for g in gates) + "</span>")


def view_deals(st: Store, now: datetime, want: str) -> str:
    models, broken, seen = [], [], {}
    for s in st.snapshots():
        seen[s["listing_ref"]] = seen.get(s["listing_ref"], 0) + 1
        if seen[s["listing_ref"]] > 1:
            continue
        try:
            m = deal_model(st, s["id"], now, want)
        except ImmutableViolation:
            broken.append(s["id"])
            continue
        m["decision"] = s["decision"]
        models.append(m)
    if not models and not broken:
        return ('<h1>Deals</h1><p class="sub">No valuations stored yet. Run '
                '<code>python -m cardman.slice listing.json [obs.csv] --store PATH</code> to add one.</p>')
    rank = {"BUY": 0, "CHECK": 1, "OFFER": 2, "NONE": 3, "HOLD": 4, "SKIP": 5}
    models.sort(key=lambda m: (m["expired"], rank.get(m["adv"].call, 9),
                               -(m["room"] if m["room"] is not None else -1e9)))
    live = [m for m in models if not m["expired"]]
    count = lambda calls: sum(m["adv"].call in calls for m in live)
    older = sum(n - 1 for n in seen.values())
    out = ['<h1>Deals</h1><p class="sub">Best calls first. Expired valuations sink to the bottom: re-evaluate '
           'them before acting.' + (f' {older} earlier valuation(s) are on each listing\'s page.' if older else "")
           + '</p>',
           '<div class="tiles">'
           f'<div class="tile"><small>Buy</small><span>{count(("BUY",))}</span><em>price and every check pass</em></div>'
           f'<div class="tile"><small>Buy after checks</small><span>{count(("CHECK",))}</span>'
           '<em>price works, a check is open</em></div>'
           f'<div class="tile"><small>Offer lower</small><span>{count(("OFFER",))}</span>'
           '<em>profitable, under target at the ask</em></div>'
           f'<div class="tile"><small>Skip or hold</small><span>{count(("SKIP", "HOLD"))}</span>'
           '<em>loses money or cannot be priced</em></div>'
           f'<div class="tile"><small>Expired</small><span>{len(models) - len(live)}</span><em>re-evaluate first</em></div>'
           '</div>']
    rows = []
    for m in models:
        a, D = m["adv"], m["D"]
        cons = a.at_ask[0] if a.at_ask else None
        ret_cls = "muted"
        if cons and cons.ret is not None and a.required is not None:
            ret_cls = "good" if cons.ret >= a.required else ("bad" if cons.profit < 0 else "warnt")
        price_cell = {"BUY": f"pay up to {D(a.pay_up_to, 'down')}", "CHECK": f"pay up to {D(a.pay_up_to, 'down')}",
                      "OFFER": f"offer ≤ {D(a.pay_up_to, 'down')}",
                      "SKIP": (f"works under {D(a.walk_away, 'down')}" if a.walk_away else "no profitable price"),
                      }.get(a.call, "—")
        rows.append(
            f'<tr class="{"expired" if m["expired"] else ""}"><td>{call_badge(a.call)}<br>'
            f'<span class="fg2">{price_cell}</span></td>'
            f'<td><a href="/deal/{e(m["sid"])}">{e(m["meta"]["listing_ref"])}</a>{SYN if m["synthetic"] else ""}'
            f'<br><code class="muted">{e(m["printing"] or "printing not resolved")}</code>'
            + ("" if m["valid"] else '<br><span class="bad">identity invalidated</span>') + '</td>'
            f'<td class="n">{D(m["ask_home"]) if m["ask_home"] is not None else "—"}</td>'
            f'<td>{charts.room_bar(m["room"])}</td>'
            f'<td class="n {ret_cls}">{(pct(float(cons.ret) * 100) if cons and cons.ret is not None else "—")}</td>'
            f'<td class="n">{(f"{a.wins}/{len(a.per_sale)}" if a.per_sale else "—")}</td>'
            f'<td>{e(a.confidence if a.confidence != "None" else "—")}</td><td>{gate_icons(m["gates"])}</td>'
            f'<td class="d">{e(datetime.fromisoformat(m["meta"]["created_at"]).astimezone().strftime("%b %d %H:%M"))}<br><span class="muted">'
            f'{"expired" if m["expired"] else "until " + when(m["meta"]["expires_at"])}</span></td>'
            f'<td>{e(m["decision"] or "pending")}</td></tr>')
    for sid in broken:
        rows.append(f'<tr><td colspan="10" class="bad">{e(sid)}: stored content does not match its id</td></tr>')
    out.append('<section><div class="wrap"><table><tr><th>Call</th><th>Listing</th><th class="n">Ask</th>'
               '<th>Room under pay-up-to</th><th class="n">Return at ask</th><th class="n">Profitable vs sales</th>'
               '<th>Confidence</th><th>Checks</th><th>Valued</th><th>Decision</th></tr>'
               + "".join(rows) + '</table></div>'
               '<p class="muted" style="margin:8px 0 0">Return at ask assumes resale at the low end of recent sales. '
               '"Profitable vs sales": in how many of the recent sales you would have made money at the ask. '
               'Checks: exact printing, authenticity, enough sales, price, listing still live.</p></section>')
    return "".join(out)


def price_zones(m: dict) -> str:
    """Where the ask sits among the three buy-price zones."""
    a, D = m["adv"], m["D"]
    if a.pay_up_to is None or a.walk_away is None:
        return ""
    top = max(a.walk_away * Decimal("1.25"), (m["ask_home"] or 0) * Decimal("1.05"))
    parts = [("Hits your target", a.pay_up_to, "--good", f"up to {html.unescape(D(a.pay_up_to, 'down'))}"),
             ("Profit, under target", a.walk_away - a.pay_up_to, "--warn",
              f"{html.unescape(D(a.pay_up_to, 'down'))} to {html.unescape(D(a.walk_away, 'down'))}"),
             ("Loses money at the low end", top - a.walk_away, "--crit",
              f"above {html.unescape(D(a.walk_away, 'down'))}")]
    segs = "".join(
        f'<div class="seg" style="flex:{float(amt):.4f} 0 0;background:var({var})" tabindex="0" '
        f'data-tip="{e(rng + chr(10) + name)}">'
        + (f'<span>{e(name)}</span>' if float(amt / top) * 480 > len(name) * 7 + 14 else "") + '</div>'
        for name, amt, var, rng in parts)
    legend = ('<table class="compact">' + "".join(
        f'<tr><td><i class="key" style="background:var({var})"></i>{e(name)}</td><td class="n">{e(rng)}</td></tr>'
        for name, amt, var, rng in parts) + '</table>')
    marker = ""
    if m["ask_home"] is not None:
        pos = min(100.0, float(m["ask_home"] / top * 100))
        marker = (f'<div class="marker" style="left:{pos:.2f}%"><b></b><span class="{"right" if pos > 70 else ""}">'
                  f'Ask {D(m["ask_home"])}</span></div>')
    ticks = (f'<div class="zticks"><span style="left:{float(a.pay_up_to / top * 100):.2f}%">{D(a.pay_up_to, "down")}</span>'
             f'<span style="left:{float(a.walk_away / top * 100):.2f}%">{D(a.walk_away, "down")}</span></div>')
    return f'<div class="split zones">{ticks}<div class="bar">{segs}</div>{marker}</div>{legend}'


def view_deal(st: Store, sid: str, now: datetime, token: str, want: str) -> str:
    m = deal_model(st, sid, now, want)
    meta, rep, a, D = m["meta"], m["rep"], m["adv"], m["D"]
    cur = D.cur
    rp = replay(st, sid)
    ident = rep.get("identity", {})
    out = [f'<h1>{e(meta["listing_ref"])} {stage_tag(meta["stage"])}{SYN if m["synthetic"] else ""}</h1>',
           f'<p class="sub"><code>{e(m["printing"] or "printing not resolved")}</code> · valued '
           f'{when(meta["created_at"])} · {"<b class=bad>expired</b> " if m["expired"] else "valid until "}'
           f'{when(meta["expires_at"])} · '
           f'{"<span class=good>✓ math replays exactly</span>" if rp["matches"] else "<span class=bad>✕ replay mismatch</span>"}'
           '</p>']
    if D.note:
        out.append(f'<div class="note">{e(D.note)}</div>')
    if m["expired"]:
        out.append('<div class="note bad">This valuation has expired: the listing may have changed or sold. '
                   'Re-evaluate before acting on it.</div>')
    if not m["valid"]:
        out.append('<div class="note bad">Identity invalidated after this valuation: '
                   + e("; ".join(m["invalid_why"])) + '. YES is refused.</div>')
    if m["synthetic"]:
        out.append('<div class="note">The sales behind this valuation are synthetic test data, not market evidence.</div>')

    # 1. the call
    if m["expired"]:
        a.todo.insert(0, "Re-evaluate first: this valuation has expired and the listing may have changed.")
    cls = CALL_STYLE.get(a.call, ("muted", ""))[0]
    out.append(f'<section class="callcard {cls}">{call_badge(a.call, big=True)}'
               f'<p class="headline">{a.title}</p>'
               + ('<ul class="because">' + "".join(f"<li>{x}</li>" for x in a.because) + '</ul>' if a.because else "")
               + (('<div class="next"><b>Before you buy</b><ol>' + "".join(f"<li>{e(t)}</li>" for t in a.todo)
                   + '</ol></div>') if a.todo else "")
               + f'<p class="conf">Confidence: <b>{e(a.confidence)}</b> · {e("; ".join(a.confidence_why))}</p>'
               + '</section>')

    # 2. the numbers that matter
    cons = a.at_ask[0] if a.at_ask else None
    out.append('<div class="tiles">'
               f'<div class="tile"><small>Ask</small><span>{D(m["ask_home"])}</span>'
               f'<em>all-in {D(a.all_in_at_ask)} with buying costs</em></div>'
               f'<div class="tile"><small>Pay up to</small><span>{D(a.pay_up_to, "down")}</span>'
               f'<em>hits the {pct(float(a.required) * 100) if a.required is not None else "—"} target</em></div>'
               f'<div class="tile"><small>Walk away above</small><span>{D(a.walk_away, "down")}</span>'
               '<em>loses money at the low-end resale</em></div>'
               f'<div class="tile"><small>Profit at the ask</small><span>{D(cons.profit) if cons else "—"}</span>'
               f'<em>{(pct(float(cons.ret) * 100) + " if it resells at the low end") if cons and cons.ret is not None else "needs a price"}</em></div>'
               f'<div class="tile"><small>Break-even resale</small><span>{D(a.breakeven_resale, "up")}</span>'
               f'<em>{(pct(float(a.cushion_pct) * 100) + " below the cheapest sale") if a.cushion_pct is not None else "—"}</em></div>'
               '</div>')

    # 3. price zones + what could go wrong
    zones = price_zones(m)
    risks = ""
    if a.flags or a.downside:
        sev = {"high": ("ic no", "!"), "medium": ("ic warnic", "!"), "info": ("ic na", "i")}
        risks = ('<section><h2>What could go wrong</h2>'
                 + ('<ul class="flags">' + "".join(f'<li><span class="{sev[s][0]}">{sev[s][1]}</span>{e(t)}</li>'
                                                   for s, t in a.flags) + '</ul>' if a.flags else "")
                 + ('<table class="compact"><tr><th>If it resells</th><th class="n">for</th><th class="n">profit</th>'
                    '<th class="n">return</th></tr>'
                    + "".join(f'<tr><td>{e(o.label)}</td><td class="n">{D(o.resale)}</td>'
                              f'<td class="n {"bad" if o.profit < 0 else ""}">{D(o.profit)}</td>'
                              f'<td class="n">{pct(float(o.ret) * 100) if o.ret is not None else "—"}</td></tr>'
                              for o in a.downside) + '</table>' if a.downside else "")
                 + '</section>')
    if zones:
        out.append('<div class="cols"><section><h2>Your buy price</h2><p class="why">Green: you hit your target. '
                   'Amber: still profitable at the low-end resale, but under target. Red: you lose money if it '
                   'resells at the low end. The black line is the ask.</p>' + zones + '</section>' + risks + '</div>')
    elif risks:
        out.append(risks)

    # 4. at the ask, against every recent sale
    if a.per_sale:
        rows = [(o.label, D.f(o.profit), f"resold at {html.unescape(D(o.resale))}"
                 + (f" · {float(o.ret) * 100:.0f}%" if o.ret is not None else "")) for o in a.per_sale]
        target = (("Target", D.f(a.required * a.all_in_at_ask)) if a.required is not None and a.all_in_at_ask else None)
        out.append(f'<section><h2>If you buy at the ask: profit against each recent sale</h2>'
                   f'<p class="why">Each bar takes one real recent sale as your resale price, after every cost. '
                   f'Past the black line you beat your target. Profit in {a.wins} of {len(a.per_sale)}; '
                   f'target hit in {a.hits} of {len(a.per_sale)}.</p>'
                   + charts.hbars(rows, cur, width=1060, label="Profit at the ask against each recent sale",
                                  target=target) + '</section>')

    # 5. sales over time
    out.append('<section><h2>Sales, ask and pay-up-to over time</h2>'
               f'<p class="why">Every record for this printing in {e(cur)}. Filled marks are the sales the valuation '
               'used; hollow ones were seen but not used (hover for why); crossed ones were cancelled or refunded.</p>'
               + (charts.price_legend(m["points"], m["refs"])
                  + charts.price_chart(m["points"], m["refs"], currency=cur, valued_at=m["created"],
                                       label="Sales, ask and pay-up-to over time")
                  if m["points"] else '<p class="note">No records to plot: '
                  + ("the printing is not resolved, so no sales can be matched to it."
                     if not m["printing"] else "no records for this printing yet.") + '</p>')
               + (f'<p class="muted">{m["unplotted"]} record(s) not plotted: no exchange rate for their date in the '
                  'listing file.</p>' if m["unplotted"] else "")
               + '</section>')

    # 6. the math, step by step + assumptions
    if a.steps:
        ci = m["ci"]
        acq, ex = ci["acquisition"], ci["exit"]
        assumptions = [
            ("Target return", pct(float(ci["target_return"]) * 100)),
            ("Capital cost", f"{float(ci['capital_cost_monthly']) * 100:.1f}% a month × {ci['holding_months']} months"),
            ("Selling fee", pct(float(ex["platform_fee_pct"]) * 100) + (f" + {D(ex['platform_fee_fixed'])}"
                                                                         if ex["platform_fee_fixed"] else "")),
            ("Payment fee (selling)", pct(float(ex["payment_fee_pct"]) * 100)),
            ("Returns reserve", pct(float(ex["returns_reserve_pct"]) * 100)),
            ("Payout FX spread", pct(float(ex["payout_fx_spread_pct"]) * 100)),
            ("Shipping out", D(ex["outbound_shipping"])),
            ("Shipping in", D(acq["inbound_shipping"])),
            ("Payment FX spread (buying)", pct(float(acq["payment_fx_spread_pct"]) * 100)),
            ("FX spread on shipping in", pct(float(acq.get("shipping_fx_spread_pct", 0)) * 100)),
            ("Proxy fee", pct(float(acq["proxy_fee_pct"]) * 100) + (f" + {D(acq['proxy_fee_fixed'])}"
                                                                    if acq["proxy_fee_fixed"] else "")),
            ("Buyer premium", pct(float(acq["buyer_premium_pct"]) * 100)),
            ("Import duty", pct(float(acq["import_duty_pct"]) * 100)),
            ("Insurance / grading", f"{D(acq['insurance'])} / {D(acq['grading'])}"),
        ]
        out.append('<div class="cols"><section><h2>How "pay up to" is worked out</h2>'
                   '<p class="why">From the low end of recent sales back to the most you can pay the seller.</p>'
                   '<table class="compact math">'
                   + "".join(f'<tr class="{"total" if s.startswith("=") else ""}"><td>{e(s)}</td>'
                             f'<td class="fg2">{e(f)}</td><td class="n">{v}</td></tr>' for s, f, v in a.steps)
                   + '</table></section><section><h2>Assumptions used</h2><p class="why">From the listing file at '
                   'valuation time. Change them there and re-evaluate.</p><table class="compact">'
                   + "".join(f'<tr><td>{e(k)}</td><td class="n">{v}</td></tr>' for k, v in assumptions)
                   + '</table></section></div>')

    # 7. where a resale goes / sensitivity
    if a.pay_up_to is not None and m["scen"]["conservative"] is not None and m["ci"]:
        acq_c = AcquisitionCosts(**m["ci"]["acquisition"])
        ex_c = ExitCosts(**m["ci"]["exit"])
        g = m["scen"]["conservative"]
        net = ex_c.net(g)
        allin_c = acq_c.all_in(a.pay_up_to)
        parts = [("Paid to the seller", D.f(a.pay_up_to), "--s1", "the pay-up-to price"),
                 ("Buying costs", D.f(allin_c - a.pay_up_to), "--s2", "shipping in, FX spread, proxy, duty"),
                 ("Your profit (target)", D.f(net - allin_c), "--s3", "what the pay-up-to price protects"),
                 ("Selling costs", D.f(g - net), "--s4", "fees, shipping out, returns reserve")]
        left = ('<section><h2>Where a low-end resale goes</h2>'
                f'<p class="why">A resale at {D(g)} split up when you pay exactly {D(a.pay_up_to, "down")}. '
                'The black line is the ask on the same scale.</p>'
                + charts.split_bar(parts, D.f(g), cur, ("Ask", D.f(m["ask_home"])) if m["ask_home"] is not None else None)
                + '</section>')
        sens = {k: v for k, v in (rep.get("sensitivities") or {}).items() if k != "base" and v is not None}
        right = ""
        if sens:
            rows = []
            for k, v in sens.items():
                new = a.pay_up_to + Decimal(str(v))
                extra = f"now {html.unescape(D(new, 'down'))}"
                if m["ask_home"] is not None:
                    extra += "; ask fits" if m["ask_home"] <= new else "; ask too high"
                rows.append((SENS_LABEL.get(k, k.replace("_", " ")), D.f(v), extra))
            rows.sort(key=lambda r: r[1])
            right = ('<section><h2>What moves the pay-up-to price</h2><p class="why">If one assumption is wrong. '
                     'Double-check the biggest one before saying YES.</p>'
                     + charts.hbars(rows, cur, width=560, label="Pay-up-to sensitivity") + '</section>')
        out.append(f'<div class="cols">{left}{right}</div>')

    # 8. decision
    hist = st.decisions(sid)
    opts = '<option value="">Choose</option>' + "".join(
        f'<option value="{x.value}"{" disabled" if x == Action.YES and not m["valid"] else ""}>{x.value}</option>'
        for x in Action)
    out.append('<section><h2>Your decision</h2>'
               + ('<table><tr><th>Action</th><th>Rationale</th><th>Wait trigger</th><th>When</th></tr>'
                  + "".join(f"<tr><td>{e(x)}</td><td>{e(r)}</td><td>{e(w or '—')}</td><td>{when(t)}</td></tr>"
                            for x, r, w, t in hist) + "</table>" if hist else "")
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

    # 9. evidence table
    rows = []
    for c in m["comps"]:
        rows.append(f'<tr><td><span class="ic ok" aria-label="used">✓</span></td><td class="d">{day(c["date"])}</td>'
                    f'<td>{e(c["type"])}</td><td>{e(c["source"])}{SYN if c.get("synthetic") else ""}</td>'
                    f'<td class="n">{money(c["amount"], c["currency"])}</td><td class="n">{D(c["home"])}</td>'
                    f'<td class="fg2">used</td>'
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
               '<p class="why">A record only counts if it is a completed sale of this exact printing, a single copy, '
               'the same grade, still valid, and dated before the valuation. Resale prices exclude shipping the '
               'buyer paid.</p><div class="wrap"><table><tr><th></th><th>Date</th><th>Type</th><th>Source</th>'
               f'<th class="n">Amount</th><th class="n">{e(cur)}</th><th>Used?</th><th>Record</th></tr>'
               + "".join(rows) + '</table></div>'
               + ('' if rows else '<p class="muted">No records.</p>') + '</section>')

    # 10. identity + history
    out.append('<div class="cols"><section><h2>Identity</h2>'
               f'<p>{icon(ident.get("status") == "RESOLVED")} {e(ident.get("status"))}</p>'
               + "".join(f"<div><code>{e(c)}</code></div>" for c in ident.get("candidates", []))
               + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in ident.get("evidence", [])) + "</ul>"
                  if ident.get("evidence") else "")
               + "".join(f'<div class="bad">{e(x)}</div>' for x in ident.get("blocking", []))
               + "".join(f'<div class="fg2">needs: {e(x)}</div>' for x in ident.get("evidence_needed", []))
               + '</section>')
    hist_rows = []
    for x in [x for x in st.snapshots() if x["listing_ref"] == meta["listing_ref"]]:
        try:
            xm = deal_model(st, x["id"], now, want)
        except ImmutableViolation:
            continue
        idcell = (f'<code>{e(x["id"][:10])}</code> (this)' if x["id"] == sid
                  else f'<a href="/deal/{e(x["id"])}"><code>{e(x["id"][:10])}</code></a>')
        hist_rows.append(f'<tr><td class="d">{when(x["created_at"])}</td><td>{call_badge(xm["adv"].call)}</td>'
                         f'<td class="n">{xm["D"](xm["ceiling"], "down")}</td><td class="n">{len(xm["comps"])}</td>'
                         f'<td>{e(x["decision"] or "pending")}</td><td>{idcell}</td></tr>')
    out.append('<section><h2>Valuations of this listing</h2><div class="wrap"><table><tr><th>Valued</th><th>Call</th>'
               '<th class="n">Pay up to</th><th class="n">Sales</th><th>Decision</th><th>Snapshot</th></tr>'
               + "".join(hist_rows) + '</table></div></section></div>')
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

        def _cur(self) -> str:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "cur" and v in CURRENCIES:
                    return v
            return CURRENCIES[0]

        def _page(self, nav, title, body, qs):
            u = urlsplit(self.path)
            back = u.path + ("?" + urlencode({k: v[0] for k, v in qs.items() if k not in ("msg", "err")})
                             if any(k not in ("msg", "err") for k in qs) else "")
            self._send(200, page(title, body, nav=nav, store=store_path, cur=self._cur(), path=back,
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
            if parts == ["currency"]:
                st.db.close()
                c = qs.get("c", [""])[0]
                back = qs.get("back", ["/"])[0]
                if not back.startswith("/") or back.startswith("//"):
                    back = "/"
                self.send_response(303)
                if c in CURRENCIES:
                    self.send_header("Set-Cookie", f"cur={c}; Path=/; SameSite=Lax; Max-Age=31536000")
                self.send_header("Location", back)
                self.send_header("Content-Length", "0")
                return self.end_headers()
            try:
                if not parts:
                    self._page("deals", "Deals", view_deals(st, now, self._cur()), qs)
                elif parts[0] == "deal" and len(parts) == 2:
                    self._page("deals", parts[1], view_deal(st, parts[1], now, token, self._cur()), qs)
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
