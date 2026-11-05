"""Local review UI over the evidence / decision store.

usage: python -m cardman.ui [--store data/store/evidence.sqlite] [--port 8765]

Binds to 127.0.0.1 only. Standard library only, server-rendered pages, no JavaScript.
Every value shown comes from the store and is escaped; source links open only for http(s).
Nothing here buys or bids: YES records that a person authorized the next manual step.
"""
from __future__ import annotations

import argparse
import html
import secrets
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote as _quote, unquote, urlencode, urlsplit

from .evidence import import_manual_csv, select_comps
from .store import ImmutableViolation, Store, replay
from .workflow import Action, Decision

STATUSES = ("CANCELLED", "REFUNDED", "RELISTED", "INVALID", "DUPLICATE", "VALID")
MAX_UPLOAD = 5 * 1024 * 1024
SYN = ' <span class="tag syn">synthetic</span>'
INVALID = ' <span class="bad">identity invalidated</span>'


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
    s = f"{d:,.0f}" if abs(d) >= 100 else f"{d:,.2f}"
    return e(f"{s} {cur}".strip())


def when(v) -> str:
    if not v:
        return "—"
    dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return e(dt.astimezone().strftime("%Y-%m-%d %H:%M"))


def link(url: str | None, text: str | None = None) -> str:
    if url and urlsplit(url).scheme in ("http", "https"):
        return f'<a href="{e(url)}" rel="noreferrer noopener" target="_blank">{e(text or "source")}</a>'
    return f'<span class="muted">{e(url or "—")}</span>'


def stage_tag(stage: str | None) -> str:
    return f'<span class="tag {e((stage or "").lower())}">{e(stage or "—")}</span>'


def q(**kw) -> str:
    kw = {k: v for k, v in kw.items() if v}
    return ("?" + urlencode(kw)) if kw else ""


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e6;--soft:#f5f5f7;--accent:#2457c5;
--ok:#1e7a3a;--warn:#9a5b00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e8e8ea;--muted:#9a9aa0;--line:#2e2e32;
--soft:#1f1f22;--accent:#7aa2ff;--ok:#5cc47e;--warn:#e0a84a;--bad:#ff7a70}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",
"Noto Sans TC","Noto Sans JP",sans-serif}
header{border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:18px;align-items:baseline;
flex-wrap:wrap}
header b{font-size:15px}header a{color:var(--fg);text-decoration:none}header a.on{color:var(--accent)}
header .store{margin-left:auto;color:var(--muted);font-size:12px}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:4px 0 12px}h2{font-size:14px;margin:24px 0 8px;text-transform:uppercase;
letter-spacing:.04em;color:var(--muted)}
a{color:var(--accent)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}td.d{white-space:nowrap}
.wrap{overflow-x:auto}
.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.tag{display:inline-block;padding:0 6px;border-radius:3px;font-size:12px;border:1px solid var(--line)}
.tag.qualified{color:var(--ok);border-color:currentColor}.tag.spectate{color:var(--warn);border-color:currentColor}
.tag.syn{color:var(--warn)}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);margin:8px 0}
.figs div{background:var(--bg);padding:10px 12px}.figs small{display:block;color:var(--muted);font-size:12px}
.figs span{font-size:18px;font-variant-numeric:tabular-nums}
.note{padding:8px 12px;border-left:3px solid var(--line);background:var(--soft);margin:8px 0}
.note.bad{border-color:var(--bad)}.note.ok{border-color:var(--ok)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}@media(max-width:760px){.cols{grid-template-columns:1fr}}
form.inline{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin:8px 0}
label{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--muted)}
input,select,textarea,button{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);
border-radius:4px;padding:5px 8px}
textarea{width:100%;min-height:60px}
button{cursor:pointer;background:var(--soft)}button:hover{border-color:var(--muted)}
button:disabled{opacity:.5;cursor:not-allowed}
ul{margin:4px 0;padding-left:18px}
code{font-size:12px}
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
            f'<main>{flash}{body}</main></body></html>').encode()


def ask_cell(rep: dict, lst: dict) -> str:
    ask = rep.get("ask") or lst.get("ask") or {}
    if not ask:
        return "—"
    home = lst.get("home_currency", "TWD")
    s = money(ask.get("amount"), ask.get("currency", ""))
    if ask.get("home") and ask.get("currency") != home:
        s = f'{money(ask["home"], home)}<br><span class="muted">{s}</span>'
    return s


# -- views ------------------------------------------------------------------------------------

def view_deals(st: Store, now: datetime) -> str:
    rows, seen = [], {}
    for s in st.snapshots():
        seen[s["listing_ref"]] = seen.get(s["listing_ref"], 0) + 1
        if seen[s["listing_ref"]] > 1:
            continue
        try:
            snap = st.snapshot(s["id"])
        except ImmutableViolation:
            rows.append(f'<tr><td colspan="8" class="bad">{e(s["id"])}: content does not match its id</td></tr>')
            continue
        rep, lst = snap.get("report", {}), snap.get("listing", {})
        ask = lst.get("ask") or {}
        ok, _ = st.snapshot_validity(s["id"])
        expired = s["expires_at"] and datetime.fromisoformat(s["expires_at"]) <= now
        syn = any(c.get("synthetic") for c in rep.get("comps", []))
        blocked = rep.get("status", {}).get("blocked_by", [])
        rows.append(
            f'<tr><td><a href="/deal/{e(s["id"])}">{e(s["listing_ref"])}</a>'
            f'{SYN if syn else ""}'
            f'{"" if ok else INVALID}</td>'
            f'<td>{stage_tag(s["stage"])}</td>'
            f'<td class="n">{ask_cell(rep, lst)}</td>'
            f'<td class="n">{money(rep.get("buy_ceiling", {}).get("ceiling"), lst.get("home_currency", "TWD"))}</td>'
            f'<td class="n">{len(rep.get("comps", []))}</td>'
            f'<td>{e("; ".join(b.split(":")[0] for b in blocked)) or "—"}</td>'
            f'<td class="d">{when(s["created_at"])}<br><span class="{"muted" if expired else ""}">'
            f'{"expired" if expired else "until " + when(s["expires_at"])}</span></td>'
            f'<td>{e(s["decision"] or "pending")}</td></tr>')
    if not rows:
        return ('<h1>Deals</h1><p class="muted">No valuations stored yet. Run '
                '<code>python -m cardman.slice listing.json [obs.csv] --store PATH</code> to add one.</p>')
    older = sum(n - 1 for n in seen.values())
    note = (f'<p class="muted">Latest valuation per listing; {older} earlier one(s) are on each listing\'s page.</p>'
            if older else "")
    return (f'<h1>Deals</h1>{note}<div class="wrap"><table><tr><th>Listing</th><th>Stage</th><th class="n">Ask</th>'
            '<th class="n">Buy ceiling</th><th class="n">Comps</th><th>Blocked by</th><th>Evaluated</th>'
            f'<th>Decision</th></tr>{"".join(rows)}</table></div>')


def view_deal(st: Store, sid: str, now: datetime, token: str) -> str:
    meta = st.snapshot_meta(sid)
    snap = st.snapshot(sid)
    rep, lst = snap.get("report", {}), snap.get("listing", {})
    home = lst.get("home_currency", "TWD")
    ok, why = st.snapshot_validity(sid)
    rp = replay(st, sid)
    ask = lst.get("ask") or {}
    sc = rep.get("exit_scenarios", {})
    bc = rep.get("buy_ceiling", {})
    expired = meta["expires_at"] and datetime.fromisoformat(meta["expires_at"]) <= now
    out = [f'<h1>{e(meta["listing_ref"])} {stage_tag(meta["stage"])}</h1>',
           f'<p class="muted">Snapshot <code>{e(sid)}</code> · evaluated {when(meta["created_at"])} · '
           f'{"expired " if expired else "valid until "}{when(meta["expires_at"])}</p>']
    if not ok:
        out.append('<div class="note bad">Identity invalidated after this valuation: '
                   + e("; ".join(why)) + '. YES is refused.</div>')
    out.append(f'<div class="note {"ok" if rp["matches"] else "bad"}">'
               + ("Valuation replays exactly from its stored inputs." if rp["matches"]
                  else "Replay does not match the recorded valuation.") + '</div>')
    out.append('<div class="figs">'
               f'<div><small>Ask</small><span>{ask_cell(rep, lst)}</span></div>'
               f'<div><small>Buy ceiling</small><span>{money(bc.get("ceiling"), home)}</span></div>'
               f'<div><small>Conservative exit</small><span>{money(sc.get("conservative"), home)}</span></div>'
               f'<div><small>Base exit</small><span>{money(sc.get("base"), home)}</span></div>'
               f'<div><small>Upside exit</small><span>{money(sc.get("upside"), home)}</span></div></div>')
    if bc.get("ceiling") is None and bc.get("reason"):
        out.append(f'<p class="muted">No ceiling: {e(bc["reason"])}</p>')
    blocked = rep.get("status", {}).get("blocked_by", [])
    if blocked:
        out.append("<h2>Blocked by</h2><ul>" + "".join(f"<li>{e(b)}</li>" for b in blocked) + "</ul>")

    # decision
    hist = st.decisions(sid)
    out.append("<h2>Decision</h2>")
    if hist:
        out.append('<table><tr><th>Action</th><th>Rationale</th><th>Wait trigger</th><th>When</th></tr>'
                   + "".join(f"<tr><td>{e(a)}</td><td>{e(r)}</td><td>{e(w or '—')}</td><td>{when(t)}</td></tr>"
                             for a, r, w, t in hist) + "</table>")
    opts = '<option value="">Choose</option>' + "".join(
        f'<option value="{a.value}"{" disabled" if a == Action.YES and not ok else ""}>{a.value}</option>'
        for a in Action)
    out.append(f'<form class="inline" method="post" action="/deal/{e(sid)}/decide">'
               f'<input type="hidden" name="t" value="{e(token)}">'
               f'<label>Action<select name="action" required>{opts}</select></label>'
               '<label style="flex:1 1 260px">Rationale<input name="rationale" required></label>'
               '<label style="flex:1 1 200px">Wait trigger (for WAIT)<input name="wait_trigger"></label>'
               '<button>Record</button></form>'
               '<p class="muted">YES records your authorization for the next manual step. Nothing is bought or bid.</p>')
    out.append(f'<form class="inline" method="post" action="/deal/{e(sid)}/reevaluate">'
               f'<input type="hidden" name="t" value="{e(token)}">'
               '<button>Re-evaluate now from the store</button>'
               '<span class="muted">New snapshot with the current evidence and status events.</span></form>')

    others = [x for x in st.snapshots() if x["listing_ref"] == meta["listing_ref"]]
    if len(others) > 1:
        out.append('<h2>Valuations of this listing</h2><table><tr><th>Evaluated</th><th>Stage</th>'
                   '<th>Decision</th><th>Snapshot</th></tr>' + "".join(
                       f'<tr><td class="d">{when(x["created_at"])}</td><td>{stage_tag(x["stage"])}</td>'
                       f'<td>{e(x["decision"] or "pending")}</td><td>'
                       + (f'<code>{e(x["id"])}</code> (this)' if x["id"] == sid
                          else f'<a href="/deal/{e(x["id"])}"><code>{e(x["id"])}</code></a>') + '</td></tr>'
                       for x in others) + '</table>')

    # identity
    ident = rep.get("identity", {})
    out.append('<h2>Identity</h2><div class="cols"><div>'
               f'<p>{e(ident.get("status"))}</p>'
               + "".join(f"<div><code>{e(c)}</code></div>" for c in ident.get("candidates", []))
               + '</div><div>'
               + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in ident.get("evidence", [])) + "</ul>"
                  if ident.get("evidence") else "")
               + "".join(f'<div class="bad">{e(x)}</div>' for x in ident.get("blocking", []))
               + "".join(f'<div class="warn">needs: {e(x)}</div>' for x in ident.get("evidence_needed", []))
               + "</div></div>")

    # comps
    comps = rep.get("comps", [])
    pool = rep.get("comp_pool", {})
    out.append(f'<h2>Comparable sales ({len(comps)}'
               + (f' of {e(pool.get("observations"))} in {e(pool.get("source"))}' if pool else "") + ')</h2>')
    if comps:
        out.append('<div class="wrap"><table><tr><th>Sold</th><th>Type</th><th>Source</th><th class="n">Amount</th>'
                   f'<th class="n">{e(home)}</th><th></th></tr>'
                   + "".join(
                       f'<tr><td class="d">{when(c["date"])}</td><td>{e(c["type"])}</td><td>{e(c["source"])}'
                       f'{SYN if c.get("synthetic") else ""}</td>'
                       f'<td class="n">{money(c["amount"], c["currency"])}</td><td class="n">{money(c["home"])}</td>'
                       f'<td><a href="/obs/{quote(c["id"])}">record</a> · {link(c.get("url"))}</td></tr>'
                       for c in comps) + "</table></div>")
    rej = rep.get("rejected_evidence", [])
    if rej:
        out.append(f'<h2>Not used ({len(rej)})</h2><table><tr><th>Record</th><th>Why</th></tr>'
                   + "".join(f'<tr><td><a href="/obs/{quote(str(i))}">{e(i)}</a></td><td>{e(r)}</td></tr>'
                             for i, r in rej) + "</table>")

    # economics
    if bc.get("ceiling") is not None or rep.get("sensitivities"):
        skip = {"ceiling", "calc_version", "reason"}
        out.append('<div class="cols"><div><h2>Ceiling breakdown</h2><table>'
                   + "".join(f'<tr><td>{e(k)}</td><td class="n">{e(v)}</td></tr>'
                             for k, v in bc.items() if k not in skip)
                   + f'<tr><td class="muted">calc</td><td class="n muted">{e(bc.get("calc_version"))}</td></tr>'
                   '</table></div><div><h2>Ceiling sensitivity</h2><table>'
                   + "".join(f'<tr><td>{e(k.replace("_", " "))}</td><td class="n">{money(v)}</td></tr>'
                             for k, v in (rep.get("sensitivities") or {}).items())
                   + "</table></div></div>")
    return "".join(out)


def view_evidence(st: Store, now: datetime, printing: str | None) -> str:
    printings = st.printing_ids()
    opts = '<option value="">All printings</option>' + "".join(
        f'<option{" selected" if p == printing else ""}>{e(p)}</option>' for p in printings)
    out = ['<h1>Evidence</h1><form class="inline" method="get" action="/evidence">'
           f'<label>Printing<select name="printing">{opts}</select></label><button>Show</button></form>']
    obs = st.observation_list(printing, as_of=now)
    verdict: dict[str, str] = {}
    if printing:
        comps, rejected = select_comps(st.observations_for(printing, as_of=now), printing, as_of=now,
                                       include_synthetic=True)
        verdict = {o.id: "comp" for o in comps}
        verdict.update({i: r for i, r in rejected})
        out.append(f'<p class="muted">Raw-condition view as of now: {len(comps)} would be used as comparables '
                   '(synthetic rows included here, refused in valuations unless allowed).</p>')
    if not obs:
        out.append('<p class="muted">No observations stored. Use Import to add a manual CSV.</p>')
        return "".join(out)
    rows = []
    for o in obs:
        st_cls = "" if o.status == "VALID" else "bad"
        v = verdict.get(o.id)
        rows.append(
            f'<tr><td><a href="/obs/{quote(o.id)}">{e(o.id)}</a>'
            f'{SYN if o.synthetic_fixture else ""}</td>'
            f'<td class="d">{when(o.event_time)}</td><td>{e(o.evidence_type.value)}</td><td>{e(o.source)}</td>'
            f'<td class="n">{money(o.original_amount, o.currency)}</td>'
            f'<td>{e(" ".join(x for x in (o.grade_company, o.grade) if x) or o.observed_condition or "raw")}</td>'
            + ("" if printing else f'<td><code>{e(", ".join(o.printing_candidates))}</code></td>')
            + f'<td class="{st_cls}">{e(o.status)}</td>'
            + (f'<td class="{"ok" if v == "comp" else "muted"}">{e(v or "—")}</td>' if printing else "")
            + f'<td>{link(o.source_url)}</td></tr>')
    out.append('<div class="wrap"><table><tr><th>Record</th><th>Event</th><th>Type</th><th>Source</th>'
               '<th class="n">Amount</th><th>Grade / cond.</th>' + ("" if printing else "<th>Printing</th>") + '<th>Status</th>'
               + ("<th>Comp?</th>" if printing else "") + f'<th></th></tr>{"".join(rows)}</table></div>')
    return "".join(out)


def view_obs(st: Store, obs_id: str, now: datetime, token: str) -> str:
    orig = st.original(obs_id)
    eff = st.effective(obs_id, now)
    hist = st.status_history(obs_id)
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
    out = [f'<h1>{e(obs_id)} <span class="{"" if eff.status == "VALID" else "bad"}">{e(eff.status)}</span></h1>',
           f'<p>{link(orig.source_url, orig.source_url)}</p>',
           '<div class="cols"><div><h2>Source record (never changes)</h2><table>'
           + "".join(f'<tr><td class="muted">{e(k)}</td><td>{v if k in ("Event time", "Captured") else e(v or "—")}</td></tr>'
                     for k, v in fields_)
           + '</table></div><div><h2>Status history</h2>']
    if hist:
        out.append('<table><tr><th>Status</th><th>Reason</th><th>By</th><th>When</th></tr>'
                   + "".join(f"<tr><td>{e(s)}</td><td>{e(r)}</td><td>{e(a)}</td><td>{when(t)}</td></tr>"
                             for s, r, a, t in hist) + "</table>")
    else:
        out.append('<p class="muted">No status events. Recorded as VALID.</p>')
    opts = '<option value="">Choose</option>' + "".join(f"<option>{s}</option>" for s in STATUSES)
    out.append(f'<form method="post" action="/obs/{quote(obs_id)}/status">'
               f'<input type="hidden" name="t" value="{e(token)}">'
               f'<div class="inline" style="display:flex;gap:8px;margin:8px 0"><label>New status<select name="status" required>'
               f'{opts}</select></label></div>'
               '<label>Reason<textarea name="reason" required></textarea></label>'
               '<p><button>Record status</button></p></form>'
               '<p class="muted">Later valuations see this from now on; earlier snapshots keep what was known then.</p>'
               '</div></div>')
    return "".join(out)


def view_import(token: str) -> str:
    return ('<h1>Import</h1><p>Manual CSV, same columns as <code>examples/manual_import_TEMPLATE.csv</code>. '
            'Rows already stored are skipped; a row whose source facts changed is refused and listed.</p>'
            f'<form method="post" action="/import" enctype="multipart/form-data">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            '<p><input type="file" name="csv" accept=".csv,text/csv" required></p>'
            '<p><button>Import</button></p></form>'
            '<p class="muted">Rows whose source_url is not http(s), or marked synthetic, are stored as synthetic '
            'and never used in real valuations.</p>')


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
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
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
            try:
                if not parts:
                    self._page("deals", "Deals", view_deals(st, now), qs)
                elif parts[0] == "deal" and len(parts) == 2:
                    self._page("deals", parts[1], view_deal(st, parts[1], now, token), qs)
                elif parts == ["evidence"]:
                    self._page("evidence", "Evidence", view_evidence(st, now, qs.get("printing", [""])[0] or None), qs)
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
