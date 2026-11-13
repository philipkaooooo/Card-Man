"""Settings: display, your costs, where the data comes from, and the advanced tools kept out of the way."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from urllib.parse import urlencode

from .games import card_href


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _q(st, sql, args=()):
    try:
        return st.db.execute(sql, args).fetchone()
    except Exception:
        return None


def _ago(ts, now: datetime) -> str:
    if not ts:
        return "never"
    try:
        t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    m = (now - t).total_seconds() / 60
    return "just now" if m < 1 else f"{m:.0f} min ago" if m < 90 else f"{m / 60:.0f} h ago" if m < 72 * 60 else f"{m / 1440:.0f} days ago"


def _row(name: str, state: str, detail: str, ok: bool | None) -> str:
    dot = {True: "good", False: "crit", None: "muted"}[ok]
    return (f'<tr><td><span class="sdot {dot}"></span><b>{e(name)}</b></td><td>{e(state)}</td>'
            f'<td class="fg2">{detail}</td></tr>')


def _pkm_row() -> str:
    """Pokémon catalog status: how many cards, when it was last downloaded."""
    import sqlite3
    from .pkm_catalog import DB
    try:
        con = sqlite3.connect(DB)
        n = con.execute("SELECT COUNT(*) FROM pkm_card").fetchone()[0]
        sets = con.execute("SELECT COUNT(*) FROM pkm_set").fetchone()[0]
        built = (con.execute("SELECT v FROM pkm_meta WHERE k='built'").fetchone() or [""])[0]
        con.close()
    except Exception:
        n, sets, built = 0, 0, ""
    return _row("Pokémon cards (English)", f"{n:,} cards in {sets} sets" if n else "not downloaded yet",
                (f"updated {e(built[:10])} · guide prices from TCGplayer, leads only" if n else "run: python -m cardman.pkm_catalog"), bool(n) or None)


def view_settings(st, now: datetime, cur: str, theme: str, profile: dict | None, engine_status: dict, path: str = "/settings",
                  fill: list[dict] | None = None, focus: dict | None = None) -> str:
    def toggle(param, opts, on):
        return '<span class="curs">' + "".join(
            f'<a href="/{param}?{urlencode({"c" if param == "currency" else "t": v, "back": path})}" class="{"on" if v == on else ""}">{e(label)}</a>'
            for v, label in opts) + '</span>'
    display = ('<section><h2>Display</h2><table class="compact"><tr><td>Currency</td><td>' + toggle("currency", (("USD", "USD"), ("TWD", "TWD")), cur)
               + '</td><td class="fg2">Every price on every page.</td></tr><tr><td>Look</td><td>'
               + toggle("theme", (("market", "Market"), ("vault", "Vault")), theme)
               + '</td><td class="fg2">Vault is the high-end look and only shows listings around $280 and up, or graded.</td></tr></table></section>')

    assumed = (profile or {}).get("_assumed", True)
    costs = ('<section><h2>Your costs</h2>'
             + ('<p class="note bad"><b>Still the assumed starter numbers.</b> Every max-buy price depends on these: set your real fees and shipping.</p>'
                if assumed else '<p class="fg2">Using your saved profile.</p>')
             + (f'<p class="fg2">Target return {float(profile.get("target_return", 0)) * 100:.0f}% · cost of money '
                f'{float(profile.get("capital_cost_monthly", 0)) * 100:.1f}%/month · amounts in {e(profile.get("home_currency"))}</p>' if profile else "")
             + '<p><a class="btn" href="/costs">Edit costs</a></p></section>')

    last = _q(st, "SELECT at, data_json FROM engine_run ORDER BY at DESC LIMIT 1")
    lr = json.loads(last[1]) if last else {}
    routes = {r.get("name"): r for r in lr.get("routes") or []}
    sold = lr.get("ebay_sold") or {}
    watch = _q(st, "SELECT COUNT(*), SUM(sold_qty IS NOT NULL) FROM ebay_sold_watch") or (0, 0)
    auto_sales = _q(st, "SELECT COUNT(*) FROM observation WHERE source='ebay_browse:sold_count' AND evidence_type='MARKETPLACE_MARKED_SOLD'") or (0,)
    real_sold = _q(st, "SELECT COUNT(*) FROM observation WHERE synthetic=0 AND evidence_type IN ('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION')") or (0,)
    imports = _q(st, "SELECT COUNT(*) FROM import_row") or (0,)
    fx = _q(st, "SELECT mid, as_of, source FROM fx_rate WHERE base='USD' AND quote='TWD' ORDER BY as_of DESC LIMIT 1")
    keys = bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))
    webhook = bool(os.environ.get("EBAY_DELETIONS_URL") and os.environ.get("EBAY_DELETIONS_SECRET"))
    every = engine_status.get("every_min") or 0
    eng_state = ("checking now" if engine_status.get("running") else f"every {every:g} min while CardMan is open") if every else "only when you press check"
    rows = [
        _row("Engine", eng_state, f'last check {e(_ago(last[0] if last else None, now))}'
             + (f' · next in {max(0, (engine_status["next"] - now).total_seconds() / 60):.0f} min' if engine_status.get("next") else "")
             + (f' · <span class="bad">{e(engine_status["error"])}</span>' if engine_status.get("error") else ""), bool(last)),
        _row("eBay listings", "on" if (routes.get("ebay") or {}).get("on") else "off",
             e((routes.get("ebay") or {}).get("detail") or ("keys found" if keys else "add your eBay keys to .env")), (routes.get("ebay") or {}).get("on")),
        _row("eBay sales (sold counts)", f"{watch[1] or 0:,} listings watched",
             f"{auto_sales[0]:,} sales recorded so far · {sold.get('calls_today', 0)} API calls today (cap 4,000)", bool(watch[1])),
        _row("Sales you added", f"{imports[0]:,} rows imported", f"{real_sold[0]:,} real sold records in total", bool(real_sold[0]) or None),
        _row("Account deletion webhook", "connected" if webhook else "not set on this Mac",
             "eBay notifications are drained into your data on every check" if webhook else "deploy it, then set EBAY_DELETIONS_URL and _SECRET", webhook),
        _row("Exchange rates", f"1 USD = {float(fx[0]):.2f} TWD" if fx else "none yet", f"{e(fx[2])}, {e(str(fx[1])[:10])}" if fx else "fetched on each check", bool(fx)),
        _row("Guide prices (YGOPRODeck)", "on" if (routes.get("ygoprodeck") or {}).get("on") else "off", "leads only; never used as a value", None),
        _pkm_row(),
        _row("Inbox folder", "on", e((routes.get("inbox") or {}).get("detail") or "~/card-man/data/inbox"), None),
    ]
    from .card_market import coverage, coverage_line
    sources = ('<section id="sources"><h2>Data sources</h2>'
               f'<p class="fg2">What CardMan holds right now: {e(coverage_line(coverage(st)))}.</p><table class="srcs">' + "".join(rows) + '</table>'
               '<form method="post" action="/engine/tick" class="inline"><input type="hidden" name="t" value="__TOKEN__">'
               '<button>Check for new listings now</button></form></section>')

    adv = (("Deals", "/deals", "Full valuations behind escalated listings: the math step by step, YES / WAIT / NO."),
           ("Scan a listing", "/scan", "Paste any listing title to see what it could be and what each version resells for."),
           ("Evidence", "/evidence", "Every recorded sale and ask, why each is or isn't used, coverage per printing."),
           ("Results", "/results", "Your own buys, sales and lessons: what really happened after each decision."),
           ("Engine details", "/opportunities/engine", "Raw engine queue, market signals and the last check's report."))
    advanced = ('<section id="advanced"><h2>Advanced</h2><p class="why">Tools and diagnostics. Everything here also feeds the main pages.</p>'
                '<ul class="advlist">' + "".join(f'<li><a href="{h}">{e(t)}</a><span class="fg2">{e(d)}</span></li>' for t, h, d in adv) + '</ul></section>')
    return '<h1>Settings</h1>' + display + costs + sources + _past_prices(fill or [], focus) + advanced


def _past_prices(fill: list[dict], focus: dict | None) -> str:
    """Getting the last few months of sales in: each link opens a site's sold results; the bookmark sends the page in."""
    from .capture import backfill_links
    def links(name_en, name_ja):
        return " · ".join(f'<a href="{e(u)}" target="_blank" rel="noreferrer noopener">{e(t)}</a>' for t, u in backfill_links(name_en, name_ja))
    top = (f'<p><b>{e(focus.get("name_en") or focus.get("name_ja"))}</b>: {links(focus.get("name_en"), focus.get("name_ja"))}</p>' if focus else "")
    rows = "".join(f'<li><a href="{e(card_href(c["passcode"]))}">{e(c["name"])}</a>: {links(c["name"], c.get("ja"))}</li>' for c in fill)
    return ('<section id="past"><h2>Fill in past prices</h2><p class="why">eBay and Yahoo! Auctions show the last 3 to 4 months of sales on '
            'their own websites. Open a link, scroll to the end of the results, then click your <b>Send to CardMan</b> bookmark '
            '(set it up once on <a href="/import">Add sales</a>). Each sale keeps its real date, and a sale seen on two sites counts once. '
            'Mercari doesn\'t show sale dates, so its sales count toward prices but not the chart.</p>' + top
            + (f'<h3>Cards worth doing now</h3><ol class="searches">{rows}</ol>' if rows else "") + '</section>')


SETTINGS_CSS = """
.curs{display:inline-flex;border:1px solid var(--axis);border-radius:6px;overflow:hidden}.curs a{padding:2px 10px;font-size:13px;color:var(--fg2);
text-decoration:none}.curs a.on{background:var(--fg);color:var(--surface)}
.sdot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;background:var(--muted)}.sdot.good{background:var(--good)}
.sdot.crit{background:var(--crit)}table.srcs td{padding:8px}.advlist{list-style:none;padding:0;margin:0;display:grid;gap:8px}
.advlist li{display:grid;grid-template-columns:160px 1fr;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
@media(max-width:600px){.advlist li{grid-template-columns:1fr}}
"""
