"""Home, card search and card pages: the storefront in front of the decision engine.

Every card shown is one passcode. Images come from this machine's cache (see cardinfo). A listing tile only shows card
artwork when every printing the listing could be shares that one artwork; otherwise it shows a neutral placeholder.
Money is shown in the display currency, converted with stored rates; a price only appears where real sales back it.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote, urlencode

from . import charts
from .fx import FxTable, StaleRateError

VAULT_MIN_HOME = Decimal("9000")      # about USD 280 at 32 TWD/USD: where the high-end view starts
THEMES = ("market", "vault")
_PC = re.compile(r"@(\d+)")


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def passcode_of(printing_id: str | None) -> str | None:
    m = _PC.search(printing_id or "")
    return m.group(1) if m else None


def img(passcode: str | None, size: str = "small", alt: str = "", cls: str = "card") -> str:
    if not passcode:
        return f'<span class="{cls} ph" role="img" aria-label="No exact card yet"><span>?</span></span>'
    q = f"?size={size}" if size else ""
    return f'<img class="{cls}" src="/img/card/{e(passcode)}.jpg{q}" alt="{e(alt)}" loading="lazy" decoding="async">'


class Money:
    """Home-currency amounts shown in the display currency at today's stored rate. Falls back to home currency."""

    def __init__(self, st, home: str, cur: str, now: datetime):
        self.home, self.cur, self.now = home, cur, now
        try:
            self.fx = FxTable(st.fx_rates(), max_age=timedelta(days=7))
        except Exception:
            self.fx = None

    def __call__(self, v, *, original: tuple | None = None) -> str:
        if v is None:
            return "—"
        try:
            if original and original[1] == self.cur:
                return e(charts.fmt(float(original[0]), self.cur))
            if self.cur != self.home and self.fx is not None:
                return e(charts.fmt(float(self.fx.convert(Decimal(str(v)), self.home, self.cur, self.now)), self.cur))
        except (StaleRateError, Exception):
            pass
        return e(charts.fmt(float(v), self.home))


def _tables(st) -> set[str]:
    return {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _search_box(q: str = "", big: bool = False) -> str:
    return (f'<form class="search{" big" if big else ""}" action="/search" method="get" role="search">'
            f'<input name="q" value="{e(q)}" placeholder="Search any card: name, 日本語, set code (LOB-EN001) or passcode" '
            f'autocomplete="off" spellcheck="false" data-suggest aria-label="Search cards"{" autofocus" if big else ""}>'
            f'<button aria-label="Search">Search</button><div class="sugg" role="listbox" hidden></div></form>')


def header_search(q: str = "") -> str:
    return _search_box(q)


_SELLER = re.compile(r"seller:[0-9a-f]{8,}")


def _move_text(text: str) -> str:
    return _SELLER.sub("A seller", text or "")


CHIP = {"UNDERPRICED": ("good", "Under max buy"), "RIGHT_PRINT_ONLY": ("warn", "Right print only"), "MISIDENTIFIED": ("warn", "Misidentified?"),
        "HIDDEN_VARIANT": ("good", "Hidden variant"), "SELLER_UNSURE": ("info", "Seller unsure"), "REFERENCE_GAP": ("info", "Under guide price"),
        "LOT": ("", "Lot"), "LOT_UNDERPRICED": ("good", "Lot under value"), "COLLECTION": ("info", "Collection"), "GRADING": ("good", "Grading play")}


def chips(kinds) -> str:
    return "".join(f'<span class="chip {CHIP.get(k, ("", ""))[0]}">{e(CHIP.get(k, ("", k.title()))[1])}</span>' for k in kinds or [])


def _listing_art(payload: dict | None) -> str | None:
    """A passcode only when every printing the listing could be is that one artwork."""
    if not payload:
        return None
    pcs = {passcode_of(s.get("printing_id")) for s in payload.get("scenarios") or []}
    pcs.discard(None)
    return pcs.pop() if len(pcs) == 1 else None


def _lot_art(n: int | None) -> str:
    return (f'<span class="card ph lotph" role="img" aria-label="Lot"><span>LOT</span>'
            f'{f"<small>{n} cards</small>" if n else ""}</span>')


def _tile(key, title, price, currency, payload, M, *, ask_home=None, sub="") -> str:
    pc = _listing_art(payload)
    is_lot = "LOT" in ((payload or {}).get("kinds") or [])
    lot_n = None
    if is_lot:
        from .listing_parser import parse_listing_text
        lot_n = parse_listing_text(title or "").lot_size
    art = _lot_art(lot_n) if is_lot else img(pc, "small", title)
    note = "" if pc or is_lot else "<span class=tl-note>could be several printings: artwork once the exact card is known</span>"
    return (f'<a class="tile-l" href="/opp/{quote(key, safe="")}">{art}'
            f'<span class="tl-body"><span class="tl-title">{e(title)}</span>'
            f'<span class="tl-price"><small>Ask</small> {M(ask_home, original=(price, currency)) if ask_home is not None else e(charts.fmt(float(price), currency)) if price else "—"}</span>'
            f'<span class="tl-sub">{sub}</span>{note}</span></a>')


def view_home(st, idx, now: datetime, cur: str, theme: str, home: str = "TWD", assumed_costs: bool = True, EM=None) -> str:
    """Search, then what needs you: Buy and Check first, then a few Watch singles. New singles and recent sales below."""
    from .ui_listing import BUY, CHECK, WATCH, decisions, row_html
    M = Money(st, home, cur, now)
    vault = theme == "vault"
    have = _tables(st)
    out = [('<section class="hero slim">'
            f'<h1>{"The Vault" if vault else "Find the exact card."}</h1>'
            + _search_box(big=True) +
            '<p class="try">Try <a href="/search?q=Blue-Eyes+White+Dragon">Blue-Eyes White Dragon</a> · '
            '<a href="/search?q=%E9%9D%92%E7%9C%BC%E3%81%AE%E7%99%BD%E9%BE%8D">青眼の白龍</a> · <a href="/search?q=LOB-EN001">LOB-EN001</a></p></section>')]
    if assumed_costs:
        out.append('<p class="note slimnote">Max-buy prices use <b>assumed</b> costs. <a href="/settings">Set your real fees and shipping</a>.</p>')

    if "opportunity" in have and EM is not None:
        items = decisions(st, EM)
        high = lambda d: (d.get("ask_home") or 0) >= float(VAULT_MIN_HOME) or re.search(r"\b(PSA|BGS|CGC|ARS|SGC)\s*\d", d.get("title") or "", re.I)
        if vault:
            items = [i for i in items if high(i[2])]
        lot = lambda d: "LOT" in (d.get("kinds") or [])
        by = lambda code, lots=False: [i for i in items if i[3].code == code and lot(i[2]) == lots]
        buy, check, watch, watch_lots = by(BUY), by(CHECK), by(WATCH), by(WATCH, True)
        rank = lambda i: (-(i[3].margin or -1e12), -(i[2].get("priority") or 0))
        top = sorted(buy, key=rank) + sorted(check, key=rank)
        from .history import sparks as _sparks
        sp = _sparks(st, now, getattr(M, 'home', 'TWD'))
        rows = [row_html(k, d, v, idx, EM, now, sp) for k, _s, d, v in top[:6]]
        rows += [row_html(k, d, v, idx, EM, now, sp) for k, _s, d, v in sorted(watch, key=rank)[:max(0, 8 - len(rows))]]
        chips = ('<div class="fchips">'
                 f'<a class="fchip" href="/opportunities?v=BUY">Buy <b>{len(buy)}</b></a>'
                 f'<a class="fchip" href="/opportunities?v=CHECK">Needs your check <b>{len(check)}</b></a>'
                 f'<a class="fchip" href="/opportunities?v=WATCH&t=singles">Watch <b>{len(watch)}</b></a>'
                 f'<a class="fchip" href="/opportunities?v=WATCH&t=lots">Lots to look at <b>{len(watch_lots)}</b></a></div>')
        out.append('<section class="shelf"><div class="shelf-h"><h2>Decisions for you</h2><a href="/opportunities">All listings →</a></div>' + chips
                   + (f'<div class="lrows">{"".join(rows)}</div>' if rows else
                      '<p class="empty">Nothing needs you right now. CardMan checks eBay every 15 minutes while it is open.</p>')
                   + ('' if buy else '<p class="muted small">No Buy yet: a Buy needs real sold prices for the exact card. eBay sold counts add them '
                      'automatically; you can also <a href="/import">add sales</a>.</p>') + '</section>')

    if "listing" in have:
        recent = st.db.execute(
            "SELECT l.key, l.title, l.price, l.currency, o.payload_json FROM listing l LEFT JOIN opportunity o ON o.key=l.key "
            "WHERE l.ended_at IS NULL AND l.kind IN ('ask','bid','auction') AND COALESCE(o.status,'')<>'REJECTED' "
            "ORDER BY l.first_seen DESC, l.rowid DESC LIMIT 300").fetchall()
        rt = []
        for key, title, price, currency, pj in recent:
            p = json.loads(pj) if pj else None
            ah = (p or {}).get("ask_home")
            if vault and not ((ah or 0) >= float(VAULT_MIN_HOME)):
                continue
            if "LOT" not in ((p or {}).get("kinds") or []) and _listing_art(p):
                rt.append(_tile(key, title, price, currency, p, M, ask_home=ah))
            if len(rt) == 6:
                break
        if rt:
            out.append('<section class="shelf"><div class="shelf-h"><h2>New single cards</h2><a href="/opportunities?t=singles">More →</a></div>'
                       f'<div class="tiles-l">{"".join(rt)}</div></section>')

    sold = st.db.execute(
        "SELECT o.id FROM observation o WHERE o.synthetic=0 AND o.evidence_type IN "
        "('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') ORDER BY o.event_time DESC LIMIT 6").fetchall()
    srows = []
    for (oid,) in sold:
        try:
            o = st.effective(oid)          # decoded record, with any confirmed printing applied
        except Exception:
            continue
        cands = o.printing_candidates
        pc = passcode_of(cands[0]) if len(cands) == 1 else None
        title = st.db.execute("SELECT title FROM listing WHERE listing_id=? LIMIT 1", (o.listing_id,)).fetchone() if "listing" in have else None
        name = ((idx.card(pc) or {}).get("name_en") if (pc and idx is not None) else None) or (title[0] if title else o.listing_id or oid)
        site = {"ebay_browse:sold_count": "eBay", "ebay": "eBay", "130point": "130point", "yahoo_auctions_jp": "Yahoo! Auctions",
                "mercari_jp": "Mercari", "manual": "you"}.get(o.source, o.source)
        srows.append(f'<li>{img(pc, "small", name)}<span><b>{e(name)}</b><br><span class="muted small">{e(site)} · {e(o.event_time.date().isoformat())}'
                     f'{"" if len(cands) == 1 else " · printing not confirmed yet"}</span></span>'
                     f'<span class="sold-p">{e(charts.fmt(float(o.original_amount), o.currency))}</span></li>')
    out.append('<section class="shelf"><div class="shelf-h"><h2>Recently sold</h2><a href="/import">Add sales →</a></div>'
               + (f'<ul class="soldlist">{"".join(srows)}</ul>' if srows else
                  '<p class="empty">No real sales recorded yet. eBay sold counts start filling this in on their own; '
                  'Yahoo! Auctions and Mercari come in with one click through <a href="/import">Add sales</a>.</p>') + '</section>')
    return "".join(out)


def view_search(idx, q: str, st=None, M=None) -> str:
    hits = idx.search(q, 48) if q else []
    mkt = {}
    if st is not None and hits and "opportunity" in _tables(st):
        from .card_market import summary
        mkt = summary(st, [h.passcode for h in hits])
    sparks = {}
    if st is not None and hits and M is not None:
        from .history import sparks as _sparks
        try:
            sp = _sparks(st, M.now, M.home)
            for pid, svg in sp.items():
                pc = pid.rsplit("@", 1)[-1].split("|")[0]
                sparks.setdefault(pc, svg)
        except Exception:
            sparks = {}
    head = ""
    if not q:
        return head + '<p class="empty">Type a card name in English or Japanese, a set code, or a passcode.</p>'
    if not hits:
        return head + f'<p class="empty">No card matches “{e(q)}”. Check the spelling, or try the set code printed under the artwork.</p>'
    tiles = []
    from collections import Counter
    same = Counter(h.name for h in hits)
    for h in hits:
        c = idx.card(h.passcode)
        prints = idx.printings(h.passcode)
        codes = sorted({p.set_code for p in prints if p.set_code})
        stat = " / ".join(str(x) for x in (c.get("atk"), c.get("def")) if x is not None)
        tiles.append(f'<a class="ctile" href="/card/{e(h.passcode)}">{img(h.passcode, "small", h.name)}'
                     f'<span class="ct-name">{e(h.name)}</span>'
                     + (f'<span class="ct-ja">{e(h.name_ja)}</span>' if h.name_ja and h.name_ja != h.name else "")
                     + f'<span class="ct-meta">{e(c.get("type_line") or c.get("card_type") or "")}{(" · " + e(stat)) if stat else ""}</span>'
                     f'<span class="ct-meta">{len(prints)} printing{"s" if len(prints) != 1 else ""}'
                     + (f' · {e(codes[0])}{" …" if len(codes) > 1 else ""}' if codes else "") + '</span>'
                     + (f'<span class="ct-mkt">{mkt[h.passcode][0]} listing{"s" if mkt[h.passcode][0] != 1 else ""} · from {M(mkt[h.passcode][1])}</span>'
                        if h.passcode in mkt and M is not None else "")
                     + (f'<span class="ct-spark">{sparks[h.passcode]}</span>' if h.passcode in sparks else "")
                     + (f'<span class="ct-why">{"alternate artwork · " if same[h.name] > 1 else ""}passcode {e(h.passcode)}</span>'
                        if same[h.name] > 1 else "")
                     + (f'<span class="ct-why">matched {e(h.matched)}</span>' if h.matched not in ("name",) else "") + '</a>')
    return (head + f'<p class="sub">{len(hits)}{"+" if len(hits) == 48 else ""} card{"s" if len(hits) != 1 else ""} for “{e(q)}”. '
            'Each result is one card (one passcode); open it for every printing.</p><div class="cgrid">' + "".join(tiles) + '</div>')


def _stats(c: dict) -> str:
    bits = []
    if c.get("attribute"):
        bits.append(("Attribute", c["attribute"]))
    for k, label in (("level", "Level"), ("rank", "Rank"), ("scale", "Pendulum scale")):
        if c.get(k) is not None:
            bits.append((label, c[k]))
    if c.get("link_arrows"):
        bits.append(("Link", f'{len(c["link_arrows"])} {"".join(c["link_arrows"])}'))
    if c.get("atk") is not None:
        bits.append(("ATK", c["atk"]))
    if c.get("def") is not None:
        bits.append(("DEF", c["def"]))
    if c.get("property"):
        bits.append(("Property", c["property"]))
    return "".join(f'<div class="stat"><small>{e(k)}</small><b>{e(v)}</b></div>' for k, v in bits)


def _para(t: str | None) -> str:
    return "".join(f"<p>{e(line)}</p>" for line in (t or "").splitlines() if line.strip())


def view_card(st, idx, passcode: str, now: datetime, cur: str, book=None, catalog=None, home: str = "TWD", token: str = "",
              filters: dict | None = None) -> str:
    c = idx.card(passcode)
    if not c:
        raise KeyError(f"no card with passcode {passcode}")
    M = Money(st, home, cur, now)
    prints = idx.printings(passcode)
    rows, lows = [], []
    from .identity import edition_variants
    all_v = [v for p in prints for v in (edition_variants(p, catalog) if catalog is not None else [p])]
    # Value only printings with a real sale recorded: asks never price anything, and there are thousands of them.
    ids = [v.id for v in all_v]
    seen: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        seen |= {r[0] for r in st.db.execute(
            "SELECT DISTINCT p.printing_id FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
            "o.evidence_type IN ('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') AND "
            f"p.printing_id IN ({','.join('?' * len(chunk))})", chunk)}
    from .card_market import THIS_CARD, listings_for
    market = listings_for(st, passcode) if "opportunity" in _tables(st) else []
    listed: dict[str, list] = {}
    for r in market:
        if r["card_prob"] >= THIS_CARD and not r["lot"]:
            listed.setdefault(r["printing_id"], []).append(r)
    method_lines = []
    for v in all_v:
        if True:
            sales = 0
            low = mid = None
            if book is not None and v.id in seen:
                try:
                    val = book.value(v.id)
                    sales = len(val.sales)
                    if val.estimate:
                        est = val.estimate
                        low, mid = est.low, est.mid
                        lows.append((low, v))
                        method_lines.append(_method_line(v, est, M, getattr(book, "bias", 1.0)))
                except Exception:
                    pass
            here = listed.get(v.id, [])
            asks = len(here)
            cheapest = min((r["all_in"] for r in here if r["all_in"] is not None), default=None)
            scan = idx.printing_scan(passcode, v)
            region = {"EN_TCG": "EN", "JP_OCG": "JP"}.get(v.region.value, v.region.value)
            rows.append(
                f'<tr><td class="scan">{"<img src=/img/scan/" + quote(scan) + " alt=" + chr(34) + e(scan) + chr(34) + " loading=lazy>" if scan else ""}</td>'
                f'<td><span class="rg {region.lower()}">{e(region)}</span></td><td><code>{e(v.set_code or "—")}</code></td>'
                f'<td>{e(v.product_name or v.product_id)}</td><td>{e(v.rarity)}</td><td>{e(v.edition or "")}</td>'
                f'<td class="n">{sales or "—"}</td><td class="n">{M(low) if low is not None else "—"}</td>'
                f'<td class="n">{M(mid) if mid is not None else "—"}</td><td class="n">{asks or "—"}</td>'
                f'<td class="n">{M(cheapest) if cheapest is not None else "—"}</td>'
                f'<td><a class="small" href="/evidence?printing={quote(v.id)}">evidence</a></td></tr>')
    names = c.get("names") or {}
    other = " · ".join(f'{e(k)}: {e(v)}' for k, v in names.items() if k not in ("en", "ja") and v)
    ja = c.get("name_ja")
    price = ""
    if lows:
        best_low, bp = min(lows, key=lambda x: x[0])
        price = (f'<div class="pricebox"><small>Market, low end of real sales</small><b>{M(best_low)}</b>'
                 f'<span>cheapest priced printing: {e(bp.set_code or bp.product_name)} {e(bp.rarity)}</span></div>')
    else:
        price = ('<div class="pricebox none"><small>Market</small><b>No sales recorded</b>'
                 '<span>Import sold prices for a printing to see real numbers here.</span></div>')
    limits = c.get("limits") or {}
    ids = [("Passcode", c["passcode"]), ("Konami ID", c.get("konami_id"))]
    ext = (f'<a href="https://yugipedia.com/wiki/?curid={e(c["yugipedia_page_id"])}" target="_blank" rel="noreferrer noopener">Yugipedia</a>'
           if c.get("yugipedia_page_id") else "")
    pend = (f'<div class="pend"><small>Pendulum effect</small>{_para(c.get("pend_en"))}</div>' if c.get("pend_en") else "")
    return (
        f'<div class="cardpage"><div class="cp-img">{img(passcode, "", c.get("name_en") or "", cls="card big")}'
        '<p class="img-note">Card artwork for this passcode. Printing scans, where an exact match exists, are in the table below.</p></div>'
        f'<div class="cp-info"><p class="crumb">{e(c.get("card_type") or "")}{" · " + e(c["type_line"]) if c.get("type_line") else ""}</p>'
        f'<h1 class="cname">{e(c.get("name_en") or ja)}</h1>'
        + (f'<p class="cja">{e(ja)}{" <span>(" + e(c["reading"]) + ")</span>" if c.get("reading") and c.get("reading") != ja else ""}</p>' if ja else "")
        + f'<div class="stats">{_stats(c)}</div>{price}'
        f'<div class="ctext">{_para(c.get("text_en"))}</div>{pend}'
        + (f'<details><summary>Japanese text</summary><div class="ctext">{_para(c.get("text_ja"))}</div></details>' if c.get("text_ja") else "")
        + '<dl class="ids">' + "".join(f"<dt>{e(k)}</dt><dd><code>{e(v)}</code></dd>" for k, v in ids if v)
        + "".join(f"<dt>{e(k.upper())} status</dt><dd>{e(v)}</dd>" for k, v in limits.items() if k in ("tcg", "ocg"))
        + (f"<dt>Source</dt><dd>{ext}</dd>" if ext else "") + '</dl>'
        + (f'<details><summary>Other languages</summary><p class="small">{other}</p></details>' if other else "")
        + (f'<p class="small muted">Series: {e(", ".join(c["series"]))}</p>' if c.get("series") else "")
        + _sold_links(c)
        + '</div></div>'
        + _history_section(st, passcode, c, all_v, now, cur, home, book)
        + _market_section(st, passcode, market, all_v, M, now, filters or {}, idx)
        + f'<section><h2>All {len(rows)} printings</h2><p class="why">From the identity catalog. Prices are only from recorded sales of that exact '
        'printing (and edition); live asks are copies for sale right now. A scan appears only when its file is that printing.</p>'
        '<div class="wrap"><table class="prints"><tr><th></th><th>Region</th><th>Set code</th><th>Product</th><th>Rarity</th><th>Edition</th>'
        '<th class="n">Sales</th><th class="n">Low</th><th class="n">Mid</th><th class="n">Listed now</th><th class="n">Lowest all-in</th><th></th></tr>'
        + "".join(rows) + '</table></div>'
        + ('<details class="dsec"><summary>How we priced it</summary>'
           + ('<ul class="method">' + "".join(method_lines) + '</ul>' if method_lines else
              '<p class="small">No printing of this card has 3 or more real sales recorded yet, so none is priced. One or two sales '
              'are shown under Recently sold but are not enough to set a price.</p>')
           + '<p class="why small">Only real sales of that exact printing and edition, raw or the same grade, never asks or guide prices. '
           'Duplicates of one sale seen on two sites count once. Every price is converted at the rate on its own sale date. '
           'Full method: docs/pricing-method.md.</p></details>')
        + '</section>' + (_sale_form(passcode, all_v, token, now) if token else ""))


def _method_line(v, est, M, bias) -> str:
    """One printing's value in plain terms: how many sales, the middle, the range, and every adjustment applied."""
    label = " · ".join(x for x in (v.set_code or "no code", v.rarity, v.edition) if x)
    adj = []
    if est.trend_monthly is not None and est.trend_monthly < 0:
        adj.append(f"falling market: priced where it is heading ({est.trend_note})")
    elif est.trend_monthly is not None:
        adj.append(f"trend {est.trend_note}: not paid for")
    if bias and bias < 1:
        adj.append(f"your own results: estimates scaled to {bias:.0%}")
    rng = ("lowest sale to middle" if est.method.startswith("sparse") else "20th to 75th percentile")
    return (f'<li><b>{e(label)}</b>: {est.n} sale{"s" if est.n != 1 else ""}, middle {M(est.mid)}, range {M(est.low)} – {M(est.high)} '
            f'({e(rng)}, recent sales weigh more){"; " + e("; ".join(adj)) if adj else ""}</li>')


GRADED = re.compile(r"\b(PSA|BGS|CGC|ARS|SGC|TAG)\b", re.I)


def history_for(st, passcode, variants, now, cur, home, book) -> dict:
    from .history import Converter, cached_build
    return cached_build(st, passcode, variants, now, Converter(st, home, cur, getattr(book, "fx", None)))


def _history_section(st, passcode, c, variants, now, cur, home, book) -> str:
    from .capture import backfill_links
    from .ui_chart import section
    return section(passcode, history_for(st, passcode, variants, now, cur, home, book), backfill_links(c.get("name_en"), c.get("name_ja")))


def _market_section(st, passcode, market, variants, M, now, f: dict, idx) -> str:
    """Every live listing of this card across platforms. Confirmed-card rows and maybe-this-card rows never mix."""
    from .card_market import SITE, THIS_CARD, coverage, coverage_line
    from .ui_listing import badge, verdict, _left, _type_label
    EM = lambda v, sign=False: M(v)
    lots = [r for r in market if r["lot"]]
    singles = [r for r in market if not r["lot"]]
    plat, prn, grd, typ = f.get("platform", ""), f.get("printing", ""), f.get("graded", ""), f.get("type", "")
    rows_in = []
    for r in singles:
        graded = bool(r["grade_company"] or GRADED.search(r["title"] or ""))
        auction = r["kind"] in ("bid", "auction")
        if (plat and r["site"] != plat) or (prn and r["printing_id"] != prn) or (grd == "raw" and graded) or (grd == "graded" and not graded) \
                or (typ == "bin" and auction) or (typ == "auction" and not auction):
            continue
        rows_in.append((r, graded, auction))
    this = [x for x in rows_in if x[0]["card_prob"] >= THIS_CARD]
    maybe = [x for x in rows_in if x[0]["card_prob"] < THIS_CARD]

    def tr(r, graded, auction):
        v = verdict(r["payload"], r["status"], EM)
        label = " · ".join((r["label"] or "").split(" · ")[:3])
        sure = r.get("printing_prob") or 0
        cond = " ".join(x for x in (r["grade_company"], r["grade"]) if x) or (r["condition"] or "")
        pc = r["payload"].get("printing_id")
        return (f'<tr><td><span class="plat {e(r["site"])}">{e(SITE[r["site"]])}</span></td>'
                f'<td><a href="/opp/{quote(r["key"], safe="")}">{e(r["title"])}</a><br><span class="muted small">{e(label)}'
                f'{"" if pc else f" · {sure:.0%} sure of the printing"}{" · " + e(cond) if cond else ""}</span></td>'
                f'<td class="n">{e(charts.fmt(float(r["price"]), r["currency"]))}</td>'
                f'<td class="n">{e(charts.fmt(float(r["shipping"]), r["currency"])) if r["shipping"] not in (None, "", "0") else "—"}</td>'
                f'<td class="n"><b>{M(r["all_in"])}</b></td><td>{"Auction" if auction else "Buy it now"}<br><span class="muted small">'
                f'{e(_left(r["payload"].get("ends_at"), now))}</span></td><td>{badge(v)}</td>'
                f'<td>{"<a href=" + chr(34) + e(r["url"]) + chr(34) + " target=_blank rel=" + chr(34) + "noreferrer noopener" + chr(34) + ">open ↗</a>" if (r["url"] or "").startswith("http") else ""}</td></tr>')

    def table(items, maybe_group=False):
        if not items:
            return ""
        head = ('<tr><th>Site</th><th>Listing</th><th class="n">Ask</th><th class="n">Shipping</th><th class="n">All-in</th><th>Type</th>'
                '<th>Verdict</th><th></th></tr>')
        body = "".join(tr(*x) for x in items[:60])
        if maybe_group:
            body = body.replace("<tr>", "<tr class=maybe>")
        return f'<div class="wrap"><table class="mkt">{head}{body}</table></div>' + (f'<p class="muted small">Showing 60 of {len(items)}.</p>' if len(items) > 60 else "")

    def chip(param, val, text):
        q = {**{k: v for k, v in f.items() if v}, param: val}
        q = {k: v for k, v in q.items() if v}
        on = (f.get(param, "") == val)
        return f'<a class="fchip{" on" if on else ""}" href="/card/{e(passcode)}?{e(urlencode(q))}#market">{e(text)}</a>'
    sites = sorted({r["site"] for r in singles})
    printings: dict[str, list] = {}
    for r in singles:
        if r["card_prob"] >= THIS_CARD and r["printing_id"]:
            printings.setdefault(r["printing_id"], [r["label"] or r["printing_id"], 0])[1] += 1
    seen: dict[str, int] = {}
    for v in printings.values():
        seen[v[0]] = seen.get(v[0], 0) + 1
    def opt_label(pid, lab):
        region = pid.split(":")[1] if pid.count(":") > 1 else ""
        return f"{lab} ({region})" if seen[lab] > 1 and region else lab
    chips = ('<div class="fchips">' + chip("platform", "", "All sites") + "".join(chip("platform", s_, SITE[s_]) for s_ in sites)
             + '<span class="sep"></span>' + chip("graded", "", "Raw and graded") + chip("graded", "raw", "Raw") + chip("graded", "graded", "Graded")
             + '<span class="sep"></span>' + chip("type", "", "Any type") + chip("type", "bin", "Buy it now") + chip("type", "auction", "Auction") + '</div>')
    if len(printings) > 1:
        # One dropdown, not a wall of chips: a card can have dozens of printings listed at once.
        keep = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in f.items() if v and k != "printing")
        opts = '<option value="">Every printing</option>' + "".join(
            f'<option value="{e(pid)}"{" selected" if pid == prn else ""}>{e(opt_label(pid, lab))} · {n} listed</option>'
            for pid, (lab, n) in sorted(printings.items(), key=lambda kv: (-kv[1][1], kv[1][0])))
        chips += (f'<form class="pfilter" method="get" action="/card/{e(passcode)}#market">{keep}<label>Printing '
                  f'<select name="printing">{opts}</select></label><button>Show</button></form>')
    sold = _sold_panel(st, variants, M)
    return (f'<section id="market"><h2>Listings now <span class="muted">({len(this)} this card{f", {len(maybe)} maybe" if maybe else ""})</span></h2>'
            f'<p class="why small">{e(coverage_line(coverage(st)))}. Sorted by all-in cost, lowest first; all-in includes shipping, fees and import '
            'at your costs.</p>' + chips
            + (table(this) if this else '<p class="empty">No live listing is certainly this card right now.</p>')
            + (f'<h3>Might be this card <span class="muted">({len(maybe)})</span></h3><p class="why small">The title fits this card and others; '
               'open one to see how likely each is.</p>' + table(maybe, True) if maybe else "")
            + (f'<p class="muted small">{len(lots)} lot(s) include this card; lots are not single-card prices. <a href="/opportunities?t=lots">See lots</a>.</p>' if lots else "")
            + '</section>' + sold)


def _sold_panel(st, variants, M) -> str:
    """Recorded real sales of this card, newest first, grouped by printing (or 'printing not confirmed')."""
    from .card_market import SITE, site_of
    ids = [v.id for v in variants]
    label = {v.id: " · ".join(x for x in (v.set_code or "no code", v.rarity, v.edition) if x) for v in variants}
    if not ids:
        return ""
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += st.db.execute(
            "SELECT DISTINCT o.id FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND o.evidence_type IN "
            f"('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') AND p.printing_id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    obs = []
    for (oid,) in rows:
        try:
            obs.append(st.effective(oid))
        except Exception:
            pass
    obs.sort(key=lambda o: o.event_time, reverse=True)
    if not obs:
        return ('<section><h2>Recently sold</h2><p class="empty">No real sale of this card recorded yet. eBay sold counts add them on their own; '
                'Yahoo! Auctions and Mercari come in through <a href="/import">Add sales</a>.</p></section>')
    groups: dict[str, list] = {}
    for o in obs[:80]:
        k = label.get(o.printing_candidates[0], "printing not confirmed") if len(o.printing_candidates) == 1 else "printing not confirmed yet"
        groups.setdefault(k, []).append(o)
    kind = {"MARKETPLACE_MARKED_SOLD": "sold", "ACCEPTED_OFFER": "accepted offer", "INDEPENDENT_TRANSACTION": "completed sale"}
    out = []
    for k, os_ in groups.items():
        out.append(f'<h3>{e(k)} <span class="muted">({len(os_)})</span></h3><ul class="soldlist">' + "".join(
            f'<li><span class="plat {e(site_of(o.source))}">{e(SITE[site_of(o.source)])}</span><span>{e(o.event_time.date().isoformat())} · '
            f'{e(kind.get(o.evidence_type.value, ""))}{" · " + e(o.grade_company + " " + (o.grade or "")) if o.grade_company else ""}</span>'
            f'<span class="sold-p">{e(charts.fmt(float(o.original_amount), o.currency))}</span></li>' for o in os_[:12]) + '</ul>')
    return '<section><h2>Recently sold</h2>' + "".join(out) + '</section>'


def _sold_links(c: dict) -> str:
    from .capture import sold_search_links
    links = sold_search_links(c.get("name_en"), c.get("name_ja"))
    if not links:
        return ""
    return ('<p class="soldlinks"><b>Find sold prices:</b> ' + " · ".join(
        f'<a href="{e(u)}" target="_blank" rel="noreferrer noopener">{e(label)}</a>' for label, u in links)
        + ' <span class="small muted">then click your “Send to CardMan” bookmark on that page</span></p>')


def _sale_form(passcode: str, variants, token: str, now: datetime) -> str:
    opts = "".join(f'<option value="{e(v.id)}">{e(" · ".join(x for x in (v.set_code or "no code", v.rarity, v.edition, v.product_name) if x))}</option>'
                   for v in variants)
    return (f'<section id="record"><h2>Record a sale</h2><p class="why">Your own sale, a private deal, or one you saw at a show. It prices '
            'the printing you choose, exactly like an imported sale. Give a link if there is one.</p>'
            f'<form method="post" action="/card/{e(passcode)}/sale" class="inline sale"><input type="hidden" name="t" value="{e(token)}">'
            f'<label>Printing<select name="printing" required>{opts}</select></label>'
            '<label>Price<input name="amount" inputmode="decimal" required size="9"></label>'
            '<label>Currency<select name="currency"><option>USD</option><option>TWD</option><option>JPY</option><option>EUR</option>'
            '<option>GBP</option></select></label>'
            f'<label>Date<input type="date" name="date" value="{now.date().isoformat()}" required></label>'
            '<label>Kind<select name="kind"><option value="transaction">Completed sale</option><option value="accepted_offer">Accepted offer'
            '</option></select></label>'
            '<label>Graded<select name="grade_company"><option value="">Raw</option><option>PSA</option><option>BGS</option><option>CGC</option>'
            '<option>ARS</option></select></label><label>Grade<input name="grade" size="4"></label>'
            '<label>Link<input name="url" type="url" placeholder="https://…" size="28"></label>'
            '<button>Record sale</button></form></section>')


def record_sale(st, catalog, passcode: str, form: dict, now: datetime) -> str:
    """A sale you witnessed, as one write-once record for exactly the printing you picked."""
    import uuid
    from .models import EvidenceType, MarketObservation
    pid = form.get("printing", "")
    base = pid.split("|")[0]
    if catalog is None or not any(p.id == base and p.artwork_id == f"art:pw{passcode}" for p in catalog.printings):
        raise ValueError("pick a printing of this card")
    try:
        amount = Decimal(str(form.get("amount", "")).replace(",", "").strip())
    except Exception:
        raise ValueError("price must be a number")
    if amount <= 0:
        raise ValueError("price must be above zero")
    cur = (form.get("currency") or "").upper()
    if cur not in ("USD", "TWD", "JPY", "EUR", "GBP"):
        raise ValueError("unknown currency")
    try:
        when = datetime.fromisoformat(form.get("date", "")).replace(tzinfo=now.tzinfo)
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    if when > now:
        raise ValueError("a sale cannot be in the future")
    url = (form.get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("link must start with http(s)://")
    kind = EvidenceType.ACCEPTED_OFFER if form.get("kind") == "accepted_offer" else EvidenceType.INDEPENDENT_TRANSACTION
    oid = f"manual:{uuid.uuid4().hex[:12]}"
    st.add_observation(MarketObservation(
        id=oid, evidence_type=kind, source="manual", original_amount=amount, currency=cur, event_time=when, capture_time=now,
        source_url=url or f"cardman://manual/{oid}", printing_candidates=(pid,), grade_company=form.get("grade_company") or None,
        grade=(form.get("grade") or "").strip() or None, synthetic_fixture=False, raw_ref="recorded in the UI"), now=now)
    return oid


HOME_CSS = """
header .hsearch{flex:1;max-width:520px}
form.search{position:relative;display:flex;gap:0;margin:0}
form.search input{flex:1;min-width:0;border-radius:999px 0 0 999px;padding:8px 14px;border-right:0}
form.search button{border-radius:0 999px 999px 0;padding:8px 16px;background:var(--fg);color:var(--surface);border-color:var(--fg);font-weight:600}
form.search.big input{font-size:17px;padding:15px 22px}form.search.big button{font-size:16px;padding:0 26px}
.sugg{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:20;background:var(--surface);border:1px solid var(--line);border-radius:12px;
box-shadow:0 12px 32px rgba(0,0,0,.14);overflow:hidden;text-align:left}
.sugg a{display:flex;gap:10px;align-items:center;padding:7px 12px;color:var(--fg);text-decoration:none}
.sugg a.on,.sugg a:hover{background:var(--soft)}.sugg img,.sugg .ph{width:30px;height:44px;flex:none}
.sugg b{display:block;font-weight:600}.sugg small{color:var(--muted)}
section.hero{text-align:center;padding:46px 12px 26px;max-width:760px;margin:0 auto;background:none;border:0}
section.hero.slim{padding:26px 12px 12px}.slimnote{max-width:760px;margin:0 auto 6px;font-size:13px}
.soldlist{list-style:none;padding:0;margin:0;display:grid;gap:8px}.soldlist li{display:grid;grid-template-columns:40px 1fr auto;gap:12px;align-items:center;
background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:8px 12px}.soldlist .card{width:40px;height:58px}
.sold-p{font-weight:700;font-variant-numeric:tabular-nums}
.hero h1{font-size:clamp(28px,5vw,40px);letter-spacing:-.02em;margin:0 0 6px;display:block}.hero>p{color:var(--fg2);font-size:16px;margin:0 0 22px}
.hero .try{font-size:13px;color:var(--muted);margin:14px 0 0}.hero .try a{color:var(--fg2)}
.shelf{background:none;border:0;padding:0;margin:26px 0}.shelf-h{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 10px}
.shelf-h h2{font-size:19px;margin:0}.shelf-h a{font-size:13px}
.tiles-l{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(250px,100%),1fr));gap:12px}
@media(max-width:800px){.shell{grid-template-columns:minmax(0,1fr)!important}header{flex-wrap:wrap}header .hsearch{order:9;flex-basis:100%;max-width:none}
header .store{display:none}form.search.big input{font-size:16px;padding:12px 16px}form.search.big button{padding:0 16px}}
.tile-l{display:flex;gap:12px;padding:12px;background:var(--surface);border:1px solid var(--line);border-radius:12px;color:var(--fg);
text-decoration:none;transition:box-shadow .15s,transform .15s}.tile-l:hover{box-shadow:0 6px 18px rgba(0,0,0,.08);transform:translateY(-1px)}
.tile-l .card{width:64px;height:93px;flex:none}.tl-body{display:flex;flex-direction:column;gap:3px;min-width:0}
.tl-title{font-weight:600;font-size:13px;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tl-price{font-size:17px;font-weight:700;font-variant-numeric:tabular-nums}.tl-sub{display:flex;flex-wrap:wrap;gap:4px}
.tl-note{font-size:11px;color:var(--muted)}
img.card,.card.ph{border-radius:4.5%/3.2%;background:var(--soft);object-fit:cover;aspect-ratio:421/614;display:block}
.card.ph{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:20px;border:1px dashed var(--axis)}
.card.lotph{flex-direction:column;gap:2px;border-style:solid;background:repeating-linear-gradient(135deg,var(--soft),var(--soft) 6px,var(--surface) 6px,var(--surface) 12px);
font-weight:800;font-size:13px;letter-spacing:.08em;color:var(--fg2)}.card.lotph small{font-size:10px;font-weight:600;letter-spacing:0}
.tl-price small{font-size:11px;font-weight:600;color:var(--muted)}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;background:var(--soft);color:var(--fg2);border:1px solid var(--line)}
.chip.good{color:var(--good-text);border-color:color-mix(in srgb,var(--good) 40%,transparent)}
.chip.warn{color:#8a5a00;border-color:color-mix(in srgb,var(--warn) 60%,transparent)}.chip.info{color:var(--accent)}
.moves{list-style:none;padding:0;margin:0;display:grid;gap:6px}.moves li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:9px 12px}
.statline{color:var(--muted);text-align:center;margin:30px 0 6px;font-size:13px}.statline b{color:var(--fg)}
.empty{color:var(--muted);padding:12px 0}
.search-page{max-width:720px;margin:4px 0 18px}
.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(168px,45%),1fr));gap:14px}
.ctile{display:flex;flex-direction:column;gap:3px;padding:10px;background:var(--surface);border:1px solid var(--line);border-radius:12px;color:var(--fg);text-decoration:none}
.ctile:hover{box-shadow:0 6px 18px rgba(0,0,0,.08)}.ctile .card{width:100%;margin:0 0 6px}
.ct-name{font-weight:600;line-height:1.25}.ct-ja{font-size:12px;color:var(--fg2)}.ct-meta{font-size:12px;color:var(--muted)}
.ct-why{font-size:11px;color:var(--accent)}.ct-mkt{font-size:12px;font-weight:650;color:var(--fg)}
.plat{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;border:1px solid var(--axis);white-space:nowrap}
.plat.ebay{color:#0064d2}.plat.yahoo{color:#b3261e}.plat.mercari{color:#d24a4a}table.mkt tr.maybe td{opacity:.85}
select{max-width:100%}form.sale{flex-wrap:wrap}form.sale label{min-width:0;max-width:100%}form.sale label select{width:100%}.pfilter{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}#market .pfilter label{display:flex;flex-direction:row;gap:6px;align-items:center;min-width:0;max-width:100%;font-size:13px;color:var(--fg2);margin:0}#market .pfilter select{min-width:0;width:auto;max-width:min(520px,70vw);margin:0}#market .pfilter button{margin:0}
.method{margin:4px 0 8px;padding-left:18px}.method li{margin:4px 0}.soldlist li .plat{justify-self:start}
.cardpage{display:grid;grid-template-columns:minmax(220px,340px) minmax(0,1fr);gap:28px;margin:0 0 22px}
@media(max-width:760px){.cardpage{grid-template-columns:1fr}.cp-img{max-width:300px}}
.card.big{width:100%;box-shadow:0 14px 40px rgba(0,0,0,.22)}.img-note{font-size:12px;color:var(--muted);margin:8px 2px}
.crumb{color:var(--muted);font-size:13px;margin:0}.cname{font-size:30px;margin:2px 0 0;display:block;letter-spacing:-.01em}
.cja{font-size:18px;margin:2px 0 12px;color:var(--fg2)}.cja span{font-size:13px;color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px}.stat{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:6px 12px;min-width:70px}
.stat small{display:block;color:var(--muted);font-size:11px}.stat b{font-size:16px}
.pricebox{border:1px solid var(--line);background:var(--surface);border-radius:12px;padding:12px 14px;margin:0 0 14px;max-width:420px}
.pricebox small{display:block;color:var(--muted);font-size:12px}.pricebox b{font-size:26px;display:block}.pricebox span{font-size:12px;color:var(--fg2)}
.pricebox.none b{font-size:18px;color:var(--fg2)}
.ctext{max-width:72ch}.ctext p{margin:0 0 8px}.pend{border-left:3px solid var(--accent);padding-left:10px;margin:0 0 10px}
.ids{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:14px 0}.ids dt{color:var(--muted);font-size:12px}.ids dd{margin:0}
table.prints td{vertical-align:middle}table.prints td.scan{width:46px}table.prints td.scan img{width:40px;border-radius:3px;display:block}
.rg{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;border:1px solid var(--axis)}.rg.jp{color:#b3261e}.rg.en{color:var(--accent)}
.themes{display:inline-flex;border:1px solid var(--axis);border-radius:6px;overflow:hidden}.themes a{padding:0 8px;font-size:12px}
.themes a.on{background:var(--fg);color:var(--surface)}
:root[data-theme="vault"]{color-scheme:dark;--bg:#0c0b09;--surface:#15130f;--fg:#f4eedf;--fg2:#d2c8b1;--muted:#8d846f;--line:#2a261f;
--axis:#3a3429;--soft:#1e1b16;--accent:#d4af6a;--good-text:#9ccf7e;--good:#6fae4f}
:root[data-theme="vault"] .hero h1,:root[data-theme="vault"] .cname,:root[data-theme="vault"] .shelf-h h2{
font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:600;letter-spacing:0}
:root[data-theme="vault"] .hero h1{color:var(--accent);font-size:44px}
:root[data-theme="vault"] form.search button{background:var(--accent);border-color:var(--accent);color:#15130f}
:root[data-theme="vault"] .tile-l,:root[data-theme="vault"] .ctile{border-color:#2f2a21;background:linear-gradient(180deg,#18150f,#13110d)}
:root[data-theme="vault"] .tl-price{color:var(--accent)}
"""

HOME_JS = r"""
(function(){document.addEventListener('keydown',function(ev){var t=ev.target;if(ev.key!=='/'||ev.metaKey||ev.ctrlKey||
/INPUT|TEXTAREA|SELECT/.test(t.tagName)||t.isContentEditable)return;var s=document.querySelector('input[data-suggest]');if(s){ev.preventDefault();s.focus();s.select()}})})();
(function(){var forms=document.querySelectorAll('form.search');forms.forEach(function(f){var inp=f.querySelector('input[data-suggest]'),
box=f.querySelector('.sugg');if(!inp||!box)return;var t=null,items=[],sel=-1,last='';
function render(list){box.textContent='';items=[];sel=-1;if(!list.length){box.hidden=true;return}
list.forEach(function(c){var a=document.createElement('a');a.href='/card/'+encodeURIComponent(c.passcode);a.setAttribute('role','option');
var im=document.createElement('img');im.src='/img/card/'+encodeURIComponent(c.passcode)+'.jpg?size=small';im.alt='';im.className='card';
var s=document.createElement('span');var b=document.createElement('b');b.textContent=c.name;s.appendChild(b);var sm=document.createElement('small');
sm.textContent=[c.ja&&c.ja!==c.name?c.ja:'',c.type||'',c.matched&&c.matched!=='name'?'matched '+c.matched:''].filter(Boolean).join(' · ');
s.appendChild(sm);a.appendChild(im);a.appendChild(s);box.appendChild(a);items.push(a)});box.hidden=false}
function mark(i){items.forEach(function(a,j){a.classList.toggle('on',j===i)});sel=i}
inp.addEventListener('input',function(){clearTimeout(t);var q=inp.value.trim();if(q.length<2){render([]);return}
t=setTimeout(function(){if(q===last)return;last=q;fetch('/api/suggest?q='+encodeURIComponent(q)).then(function(r){return r.json()})
.then(function(d){if(inp.value.trim()===q)render(d.results||[])}).catch(function(){})},110)});
inp.addEventListener('keydown',function(ev){if(box.hidden)return;if(ev.key==='ArrowDown'){ev.preventDefault();mark(Math.min(sel+1,items.length-1))}
else if(ev.key==='ArrowUp'){ev.preventDefault();mark(Math.max(sel-1,-1))}else if(ev.key==='Enter'&&sel>=0){ev.preventDefault();location.href=items[sel].href}
else if(ev.key==='Escape'){box.hidden=true}});
document.addEventListener('click',function(ev){if(!f.contains(ev.target))box.hidden=true})})})();
"""


def suggest_json(idx, q: str) -> bytes:
    return json.dumps({"results": idx.suggest(q, 8)}, ensure_ascii=False).encode()
