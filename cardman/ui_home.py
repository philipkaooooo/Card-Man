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
from .games import card_href
from .fx import FxTable, StaleRateError

VAULT_MIN_HOME = Decimal("9000")      # about USD 280 at 32 TWD/USD: where the high-end view starts
THEMES = ("market", "vault")
_PC = re.compile(r"@([^@|]+)")                  # passcode or pkm-<card id>: see games.py


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def passcode_of(printing_id: str | None) -> str | None:
    m = _PC.search(printing_id or "")
    return m.group(1) if m else None


def img(passcode: str | None, size: str = "small", alt: str = "", cls: str = "card") -> str:
    if not passcode:
        return f'<span class="{cls} ph" role="img" aria-label="No exact card yet"><span>?</span></span>'
    q = f"?size={size}" if size else ""
    if passcode.startswith("pkm-"):
        src = f"/pkm/img/pkm/{e(passcode[4:])}.png?size={'large' if size in ('', 'large') else 'small'}"
        return f'<img class="{cls} pkm" src="{src}" alt="{e(alt)}" loading="lazy" decoding="async">'
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


def _search_box(q: str = "", big: bool = False, game: str = "ygo") -> str:
    hint = ("Search any Pokémon card: name, set, or number like 199/165" if game == "pkm"
            else "Search any card: name, 日本語, set code (LOB-EN001) or card number")
    return (f'<form class="search{" big" if big else ""}" action="/search" method="get" role="search">'
            f'<input name="q" value="{e(q)}" placeholder="{e(hint)}" '
            f'autocomplete="off" spellcheck="false" data-suggest aria-label="Search cards"{" autofocus" if big else ""}>'
            f'<button aria-label="Search">Search</button><div class="sugg" role="listbox" hidden></div></form>')


def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
        if (t.tzinfo is None) != (now.tzinfo is None):
            t = t.replace(tzinfo=now.tzinfo)
        s = int((now - t).total_seconds())
    except (ValueError, TypeError):
        return ""
    if s < 0:
        return "just now"
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def header_search(q: str = "", game: str = "ygo") -> str:
    return _search_box(q, game=game)


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


def _tile(key, title, price, currency, payload, M, *, ask_home=None, sub="", photo=False) -> str:
    pc = _listing_art(payload)
    is_lot = "LOT" in ((payload or {}).get("kinds") or [])
    lot_n = None
    if is_lot:
        from .listing_parser import parse_listing_text
        lot_n = parse_listing_text(title or "").lot_size
    from .ui_listing import _art_with_ref, item_id_of
    iid = item_id_of(key, payload)
    art = (_lot_art(lot_n) if is_lot and not photo else
           _art_with_ref(key, payload or {}, None, {iid: 1} if photo and iid else {}) if photo else img(pc, "small", title))
    note = ""
    return (f'<a class="tile-l" href="/opp/{quote(key, safe="")}">{art}'
            f'<span class="tl-body"><span class="tl-title">{e(title)}</span>'
            f'<span class="tl-price"><small>Listed at</small> {M(ask_home, original=(price, currency)) if ask_home is not None else e(charts.fmt(float(price), currency)) if price else "—"}</span>'
            f'<span class="tl-sub">{sub}</span>{note}</span></a>')


def _guide_fns(idx):
    """A version's own guide price, and its own history. Never another version's."""
    cat = getattr(idx, "cat", None)
    guide = getattr(cat, "guide", None)
    hist = getattr(cat, "guide_history", None)
    if guide is not None and hist is not None:
        return guide, hist
    from .catalog_boot import version_market

    def history(pid):
        one = version_market(pid)
        return [(one[1], one[0])] if one else []

    return version_market, history


def _own_guides(idx, passcode: str) -> str:
    """Each version's guide, written out separately. No average, no cheapest-stands-for-the-card."""
    guide, _ = _guide_fns(idx)
    bits = []
    for p in (idx.printings(passcode) or [])[:5]:
        try:
            g = guide(p.id)
        except Exception:
            g = None
        if not g:
            continue
        name = p.edition or p.rarity or "Version"
        bits.append(f"{e(name)} {e(charts.fmt(g[0], 'USD'))}")
    if not bits:
        return ""
    more = ""
    return f'<span class="ct-mkt">{ " · ".join(bits) }{more}<br>guide, not a sale</span>'


_HUNT = "And The Hunt Begins..."

_CLS_OF = {"BUY": "good", "CHECK": "avg", "WATCH": "avg", "PASS": "bad"}
_WINDOW = {"1h": timedelta(hours=1), "24h": timedelta(hours=24)}


_MKT_ICON = {"ebay_browse": ("e", "#0654ba", "eBay"), "inbox": ("\u2193", "#6b7280", "Imported"),
             "facebook": ("f", "#1877f2", "Facebook Marketplace")}


def _mkt_icon(source: str) -> str:
    base = (source or "").split(":")[0]
    glyph, tint, label = _MKT_ICON.get(base, (base[:1].upper() or "?", "#8a8f98", base.replace("_", " ").title() or "Market"))
    return ('<span class="mkt" style="background:{}" title="{}" aria-label="{}" role="img">{}</span>'.format(
        tint, e(label), e(label), e(glyph)))


def _q1(f: dict, key: str, default: str) -> str:
    x = f.get(key)
    if isinstance(x, list):
        x = x[0] if x else ""
    return x if isinstance(x, str) and x in ("all", "good", "avg", "bad", "none", "1h", "24h") else default


def _hun_head(base: str, v: str, t: str, counts: dict) -> str:
    sig = (("all", "All", "hsn"), ("good", "Good", "hsg"), ("avg", "Average", "hsa"), ("bad", "Bad", "hsb"),
           ("none", "Unrated", "hsn"))
    cells = "".join('<a class="{}{}" href="{}?v={}&t={}"><small>{}</small><b>{}</b></a>'.format(
        cls, " on" if v == code else "", base, code, t, lab, counts[code]) for code, lab, cls in sig)
    fresh = "".join('<a{} href="{}?v={}&t={}">{}</a>'.format(
        ' class="on"' if t == code else "", base, v, code, lab) for code, lab in (
        ("1h", "Last hour"), ("24h", "Last 24 hours"), ("all", "Any time")))
    return ('<div class="hun-head"><div><p class="hun-kick">Freshest asks on the market, ranked by the read</p>'
            '<h2 class="hun-h">New listings</h2></div><div class="hun-stats">' + cells + "</div></div>"
            '<div class="hun-tools"><span class="hun-fresh">' + fresh + "</span>"
            '<span class="hun-shown small">12 tiles, best signals first</span>'
            '<a class="hun-all" href="/opportunities">All listings \u2192</a></div>')


def _just_posted(st, now: datetime, game: str, M=None, f: dict | None = None, base: str = "/") -> str:
    """The newest things actually posted, wherever they were posted: one tile per fresh listing, laser-coded."""
    if "listing" not in _tables(st):
        return ""
    f = f or {}
    v, t = _q1(f, "v", "all"), _q1(f, "t", "all")
    have = _tables(st)
    sel = "l.key, l.source, l.title, l.url, l.price, l.currency, l.first_seen, l.data_json, "
    sel += "o.payload_json, o.status" if "opportunity" in have else "NULL, NULL"
    sel += ", l.listing_id, d.images_json" if "listing_detail" in have else ", NULL, NULL"
    joins = ("LEFT JOIN opportunity o ON o.key=l.key " if "opportunity" in have else "")
    joins += "LEFT JOIN listing_detail d ON d.item_id=l.listing_id" if "listing_detail" in have else ""
    rows = st.db.execute(
        f"SELECT {sel} FROM listing l {joins} "
        "WHERE l.ended_at IS NULL AND l.kind IN ('ask','bid','auction') "
        "ORDER BY l.first_seen DESC LIMIT 400").fetchall()
    from .ui_listing import lphoto, verdict
    cut = now - _WINDOW[t] if t in _WINDOW else None
    counts = {"all": 0, "good": 0, "avg": 0, "bad": 0, "none": 0}
    entries = []
    for key, source, title, url, price, currency, seen, dj, payload, status, iid, imgs in rows:
        try:
            d = json.loads(payload) if payload else (json.loads(dj) if dj else {})
        except ValueError:
            d = {}
        if (d.get("game") or "ygo") != game:
            continue
        if cut:
            try:
                ts = datetime.fromisoformat(seen)
                if (ts.tzinfo is None) != (now.tzinfo is None):
                    ts = ts.replace(tzinfo=now.tzinfo)
                if ts < cut:
                    continue
            except (ValueError, TypeError):
                pass
        code = "none"
        if M is not None and payload:
            try:
                vv = verdict(d, status or "OPEN", M)
                code = _CLS_OF.get(vv.code, "none")
                if code == "bad" and not (vv.top or {}).get("basis"):
                    code = "none"      # a red laser has to be a measured red, not an absence of evidence
            except Exception:
                code = "none"
        counts["all"] += 1
        counts[code] += 1
        price_txt = charts.fmt(Decimal(str(price)), currency) if price not in (None, "") else "—"
        when = _ago(seen, now)
        if v != "all" and code != v:
            continue
        own = len(json.loads(imgs or "[]")) if imgs else 0
        art = lphoto(iid, 0, "m", title or "", cls="hun-img") if own else img(_listing_art(d), "large", title or "", cls="card hun-img")
        entries.append(({"good": 0, "avg": 1, "bad": 2}.get(code, 3), seen or "",
                        '<a class="hun" href="{}" data-key="{}" data-signal="{}">{}'
                        '<span class="hun-b"><span class="hun-name">{}</span>'
                        '<span class="hun-row"><span class="hun-price">{}</span>'
                        '<span class="hun-meta">{}{}</span></span></span>'
                        '<span class="laser l-{}"></span></a>'.format(
                            e(url), e(key), e(code), art, e((title or "")[:90]), e(price_txt),
                            _mkt_icon(source), (" " + e(when)) if when else "", e(code))))
    entries.sort(key=lambda x: x[1], reverse=True)
    entries.sort(key=lambda x: x[0])
    tiles = [h for _r, _s, h in entries[:12]]
    grid = (f'<div class="hungrid">{"".join(tiles)}</div>' if tiles else
            '<p class="empty">' + ("Nothing matches this filter yet — widen it above, or the collector has not reached this signal today."
                                   if v != "all" or t != "all" else
                                   'Nothing posted yet. The collector runs every few minutes; you can also '
                                   '<a href="/import">import saved pages</a> from any marketplace, Facebook Marketplace included.') + "</p>")
    return ('<section class="hunzone">' + _hun_head(base, v, t, counts) + grid +
            '<p class="hun-note small">Signal is the read on the price: green under what to pay, amber worth a '
            'second look, red above it; unrated has no sales history yet. Most asks sit above what a card really '
            'sells for, so red is the common read and green is rare on purpose.</p></section>')


def view_home(st, idx, now: datetime, cur: str, theme: str, home: str = "TWD", assumed_costs: bool = True, EM=None, game: str = "ygo",
              qs: dict | None = None) -> str:
    """The hunt: the title, the search, and the newest postings. Every decision view lives on its own page."""
    M = Money(st, home, cur, now)
    letters = "".join(f'<span style="--i:{i}">{c if c != " " else "&nbsp;"}</span>' for i, c in enumerate(_HUNT))
    out = [f'<section class="hero hunt"><h1 class="huntitle" aria-label="{e(_HUNT)}">{letters}</h1>' + _search_box(big=True, game=game) + "</section>"]
    out.append(_just_posted(st, now, game, M, qs or {}, "/pkm/" if game == "pkm" else "/"))
    return '<style>main.page{max-width:min(95vw,1500px)}</style>' + "".join(out)


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
                pc = pid.rsplit("@", 1)[-1].split("|")[0]          # passcode or pkm-<card id>
                sparks.setdefault(pc, svg)
        except Exception:
            sparks = {}
    head = ""
    if not q:
        return head + '<p class="empty">Type a card name in English or Japanese, a set code, or a passcode.</p>'
    if not hits:
        return head + f'<p class="empty">No card matches “{e(q)}”. Check the spelling, or try the set code printed under the artwork.</p>'
    from .character import slug as _slug, species_of
    from collections import Counter
    specs = Counter(s for h in hits for s in species_of(h.name))
    if specs and idx.card(hits[0].passcode) and (idx.card(hits[0].passcode) or {}).get("game") == "pkm":
        who, n = specs.most_common(1)[0]
        if n >= 2:
            head += f'<p class="sub"><a href="/pkm/character/{e(_slug(who))}">Browse {e(who)} market</a>. Each card keeps its own price.</p>'
    tiles = []
    from collections import Counter
    same = Counter(h.name for h in hits)
    for h in hits:
        c = idx.card(h.passcode)
        prints = idx.printings(h.passcode)
        codes = sorted({p.set_code for p in prints if p.set_code})
        stat = " / ".join(str(x) for x in (c.get("atk"), c.get("def")) if x is not None)
        if c.get("game") == "pkm":
            tiles.append(f'<a class="ctile" href="{e(card_href(h.passcode))}">{img(h.passcode, "small", h.name)}'
                         f'<span class="ct-name">{e(h.name)}</span>'
                         f'<span class="ct-meta">{e(c.get("set_name") or "")} · {e(c.get("number"))}/{e(c.get("printed_total"))}</span>'
                         f'<span class="ct-meta">{e(c.get("rarity") or "")}{" · " + e((c.get("release") or "")[:4]) if c.get("release") else ""}</span>'
                         + _own_guides(idx, h.passcode)
                         + (f'<span class="ct-mkt">{mkt[h.passcode][0]} listing{"s" if mkt[h.passcode][0] != 1 else ""} · from {M(mkt[h.passcode][1])}</span>'
                            if h.passcode in mkt and M is not None else "")
                         + (f'<span class="ct-spark">{sparks[h.passcode]}</span>' if h.passcode in sparks else "") + '</a>')
            continue
        tiles.append(f'<a class="ctile" href="{e(card_href(h.passcode))}">{img(h.passcode, "small", h.name)}'
                     f'<span class="ct-name">{e(h.name)}</span>'
                     + (f'<span class="ct-ja">{e(h.name_ja)}</span>' if h.name_ja and h.name_ja != h.name else "")
                     + f'<span class="ct-meta">{e(c.get("type_line") or c.get("card_type") or "")}{(" · " + e(stat)) if stat else ""}</span>'
                     f'<span class="ct-meta">{len(prints)} printing{"s" if len(prints) != 1 else ""}'
                     + (f' · {e(codes[0])}{" …" if len(codes) > 1 else ""}' if codes else "") + '</span>'
                     + _own_guides(idx, h.passcode)
                     + (f'<span class="ct-mkt">{mkt[h.passcode][0]} listing{"s" if mkt[h.passcode][0] != 1 else ""} · from {M(mkt[h.passcode][1])}</span>'
                        if h.passcode in mkt and M is not None else "")
                     + (f'<span class="ct-spark">{sparks[h.passcode]}</span>' if h.passcode in sparks else "")
                     + (f'<span class="ct-why">{"alternate artwork · " if same[h.name] > 1 else ""}passcode {e(h.passcode)}</span>'
                        if same[h.name] > 1 else "")
                     + (f'<span class="ct-why">matched {e(h.matched)}</span>' if h.matched not in ("name",) else "") + '</a>')
    return (head + f'<p class="sub">{len(hits)}{"+" if len(hits) == 48 else ""} card{"s" if len(hits) != 1 else ""} for “{e(q)}”. '
            'Open a card to see every version, its prices and what\'s for sale.</p><div class="cgrid">' + "".join(tiles) + '</div>')


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
    """Top to bottom: the card and a version picker, one plain price line, the chart, what's for sale, what sold.
    Nothing here explains where data comes from (that lives in Settings)."""
    c = idx.card(passcode)
    if not c:
        raise KeyError(f"no card with passcode {passcode}")
    f = filters or {}
    M = Money(st, home, cur, now)
    from .identity import edition_variants
    pkm = c.get("game") == "pkm"
    all_v = [v for p in idx.printings(passcode) for v in (edition_variants(p, catalog) if (catalog is not None and not pkm) else [p])]
    ids = [v.id for v in all_v]
    seen: dict[str, int] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for pid, n in st.db.execute(
                "SELECT p.printing_id, COUNT(DISTINCT o.id) FROM observation_printing p JOIN observation o ON o.id=p.obs_id WHERE o.synthetic=0 AND "
                "o.evidence_type IN ('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION') AND "
                f"p.printing_id IN ({','.join('?' * len(chunk))}) GROUP BY 1", chunk):
            seen[pid] = n
    from .card_market import THIS_CARD, listings_for
    market = listings_for(st, passcode) if "opportunity" in _tables(st) else []
    listed: dict[str, list] = {}
    for r in market:
        if r["card_prob"] >= THIS_CARD and not r["lot"]:
            listed.setdefault(r["printing_id"], []).append(r)
    value: dict[str, object] = {}
    for vid in seen:
        if book is not None:
            try:
                value[vid] = book.value(vid)
            except Exception:
                pass
    # Which version to show: the one asked for, else the most sold, else the most listed.
    by_id = {v.id: v for v in all_v}
    chosen = f.get("v") if f.get("v") in by_id else None
    chosen = max(all_v, key=lambda v: (len(getattr(value.get(v.id), "sales", []) or []), seen.get(v.id, 0), len(listed.get(v.id, [])))).id if chosen is None and all_v else chosen
    v = by_id.get(chosen)
    guide, guide_hist = _guide_fns(idx)
    hist = history_for(st, passcode, all_v, now, cur, home, book, guide=guide_hist)

    picker = _version_picker(passcode, all_v, chosen, seen, listed)
    price = _price_line(v, value.get(chosen), listed.get(chosen, []), hist, M, guide=guide)
    ja = c.get("name_ja")
    limits = c.get("limits") or {}
    details = [("Card number", c["passcode"]), ("Konami ID", c.get("konami_id"))] + [
        (f"Tournament status ({k.upper()})", lim) for k, lim in limits.items() if k in ("tcg", "ocg")]
    pend = f'<div class="pend"><small>Pendulum effect</small>{_para(c.get("pend_en"))}</div>' if c.get("pend_en") else ""
    from .ui_chart import section
    if pkm:
        from .character import slug as _slug, species_of
        who = species_of(c.get("name_en") or "")[0]
        top = (f'<p class="crumb">{e(c.get("card_type") or "")}{" · " + e(c["type_line"]) if c.get("type_line") else ""}</p>'
               f'<h1 class="cname">{e(c["name_en"])}</h1>'
               f'<p class="cja"><a href="/pkm/character/{e(_slug(who))}">{e(who)} market</a> · each card priced on its own</p>'
               f'<p class="cja">{e(c.get("set_name"))} · {e(c.get("number"))}/{e(c.get("printed_total"))} · {e(c.get("rarity") or "")}</p>')
        return (
            f'<div class="cardpage"><div class="cp-img">{img(passcode, "", c.get("name_en") or "", cls="card big")}</div>'
            f'<div class="cp-info">{top}' + picker + price + _pkm_details(c) + '</div></div>'
            + section(passcode, hist, chosen).replace(
                '</section>', f'<p class="small"><a href="/settings?card={e(passcode)}#past">Fill in past prices for this card</a></p></section>', 1)
            + _market_section(passcode, market, chosen, by_id, M, now, f)
            + _sold_panel(st, all_v, chosen, M)
            + _versions_table(passcode, all_v, chosen, value, listed, M)
            + (_sale_form(passcode, all_v, chosen, token, now) if token else ""))
    return (
        f'<div class="cardpage"><div class="cp-img">{img(passcode, "", c.get("name_en") or "", cls="card big")}</div>'
        f'<div class="cp-info"><p class="crumb">{e(c.get("card_type") or "")}{" · " + e(c["type_line"]) if c.get("type_line") else ""}</p>'
        f'<h1 class="cname">{e(c.get("name_en") or ja)}</h1>'
        + (f'<p class="cja">{e(ja)}{" <span>(" + e(c["reading"]) + ")</span>" if c.get("reading") and c.get("reading") != ja else ""}</p>' if ja else "")
        + picker + price
        + f'<div class="stats">{_stats(c)}</div><div class="ctext">{_para(c.get("text_en"))}</div>{pend}'
        + (f'<details><summary>Japanese text</summary><div class="ctext">{_para(c.get("text_ja"))}</div></details>' if c.get("text_ja") else "")
        + '<details><summary>Card details</summary><dl class="ids">' + "".join(f"<dt>{e(k)}</dt><dd>{e(x)}</dd>" for k, x in details if x)
        + '</dl></details></div></div>'
        + section(passcode, hist, chosen).replace('</section>', f'<p class="small"><a href="/settings?card={e(passcode)}#past">Fill in past '
                                                   'prices for this card</a></p></section>', 1)
        + _market_section(passcode, market, chosen, by_id, M, now, f)
        + _sold_panel(st, all_v, chosen, M)
        + _versions_table(passcode, all_v, chosen, value, listed, M)
        + (_sale_form(passcode, all_v, chosen, token, now) if token else ""))


def _pkm_details(c: dict) -> str:
    """What a Pokémon collector reads off the card: HP and type, abilities and attacks, weakness, resistance, retreat,
    then the set details. Nothing about where the data comes from."""
    stats = [("HP", c.get("hp")), ("Type", " / ".join(c.get("types") or []) or None),
             ("Weakness", ", ".join(f'{w.get("type")} {w.get("value")}' for w in c.get("weaknesses") or []) or None),
             ("Resistance", ", ".join(f'{w.get("type")} {w.get("value")}' for w in c.get("resistances") or []) or None),
             ("Retreat", c.get("retreat") if c.get("card_type") == "Pokémon" else None)]
    out = '<div class="stats">' + "".join(f'<div class="stat"><small>{e(k)}</small><b>{e(v)}</b></div>' for k, v in stats
                                          if v not in (None, "")) + '</div>'
    moves = []
    for a in c.get("abilities") or []:
        moves.append(f'<div class="move"><b><span class="mtag">{e(a.get("type") or "Ability")}</span> {e(a.get("name"))}</b>'
                     f'<p>{e(a.get("text"))}</p></div>')
    for a in c.get("attacks") or []:
        cost = " ".join(a.get("cost") or [])
        moves.append(f'<div class="move"><b>{e(a.get("name"))}<span class="mdmg">{e(a.get("damage") or "")}</span></b>'
                     f'<span class="muted small">{e(cost)}</span>' + (f'<p>{e(a.get("text"))}</p>' if a.get("text") else "") + '</div>')
    rules = "".join(f'<p class="small">{e(r)}</p>' for r in c.get("rules") or [])
    legal = ", ".join(k.title() for k, v in (c.get("legalities") or {}).items() if v == "Legal")
    details = [("Set", c.get("set_name")), ("Series", c.get("series")), ("Number", f'{c.get("number")}/{c.get("printed_total")}'),
               ("Rarity", c.get("rarity")), ("Artist", c.get("artist")), ("Regulation mark", c.get("regulation_mark")),
               ("Released", c.get("release")), ("Evolves from", c.get("evolves_from")), ("Legal in", legal or None)]
    return (out + (f'<div class="moves">{"".join(moves)}</div>' if moves else "") + rules
            + (f'<p class="ctext"><i>{e(c["flavor"])}</i></p>' if c.get("flavor") else "")
            + '<details open><summary>Card details</summary><dl class="ids">'
            + "".join(f"<dt>{e(k)}</dt><dd>{e(x)}</dd>" for k, x in details if x) + '</dl></details>')


def _vlabel(v) -> str:
    if v.id.startswith("pk:"):
        return v.edition or "Standard"
    region = {"EN_TCG": "", "JP_OCG": " (Japanese)"}.get(v.region.value, "")
    return " · ".join(x for x in (v.set_code or v.product_name, v.rarity, v.edition) if x) + region


def _version_picker(passcode, all_v, chosen, seen, listed) -> str:
    if len(all_v) <= 1:
        return f'<p class="vpick">{e(_vlabel(all_v[0]))}</p>' if all_v else ""
    active = [v for v in all_v if seen.get(v.id) or listed.get(v.id)]
    rest = [v for v in all_v if v not in active]
    opt = lambda v: f'<option value="{e(v.id)}"{" selected" if v.id == chosen else ""}>{e(_vlabel(v))}</option>'
    body = ((f'<optgroup label="Selling or for sale now">{"".join(opt(v) for v in sorted(active, key=_vlabel))}</optgroup>' if active else "")
            + f'<optgroup label="All other versions">{"".join(opt(v) for v in sorted(rest, key=_vlabel))}</optgroup>')
    return (f'<form class="vpick" method="get" action="{e(card_href(passcode))}"><label>Version <select name="v">{body}</select></label>'
            '<button>Show</button></form>')


def _price_line(v, val, here, hist, M, guide=None) -> str:
    """One sentence: what it sells for, or what it is listed for when there are too few sales."""
    from .history import plain_trend
    est = getattr(val, "estimate", None)
    s = next((x for x in hist["series"] if v is not None and x["printing_id"] == v.id and x["grade"] == "Raw"), None)
    trend = plain_trend(s["summary"]) if s else None
    cheapest = min((r["all_in"] for r in here if r["all_in"] is not None), default=None)
    if est is not None:
        n = len(val.sales)
        return (f'<div class="pricebox"><b>Sells for about {M(est.mid)}</b><span>{n} recent sale{"s" if n != 1 else ""} · usually '
                f'{M(est.low)} – {M(est.high)}{" · " + e(trend) if trend else ""}</span>'
                + (f'<span>For sale now from {M(cheapest)}</span>' if cheapest is not None else "") + '</div>')
    if cheapest is not None:
        return (f'<div class="pricebox"><b>Listed from {M(cheapest)} total</b><span>Not enough sales yet to say what it really sells for. '
                '"Total" includes shipping and your buying costs.</span></div>')
    g = guide(v.id) if (guide and v is not None) else None
    if g:
        return (f'<div class="pricebox none"><b>No sales seen yet</b><span>Price guides say about {e(charts.fmt(g[0], "USD"))} '
                f'(TCGplayer, {e(g[1])}). That is a guide, not a real sale.</span></div>')
    return '<div class="pricebox none"><b>No price yet</b><span>CardMan hasn\'t seen this version sell or listed for sale yet.</span></div>'


def history_for(st, passcode, variants, now, cur, home, book, guide=None) -> dict:
    from .history import Converter, cached_build
    return cached_build(st, passcode, variants, now, Converter(st, home, cur, getattr(book, "fx", None)), guide_fn=guide)


GRADED = re.compile(r"\b(PSA|BGS|CGC|ARS|SGC|TAG)\b", re.I)
SITE_NAME = {"ebay": "eBay", "yahoo": "Yahoo! Auctions", "mercari": "Mercari", "130point": "eBay", "manual": "You", "other": "Other"}


def _market_section(passcode, market, chosen, by_id, M, now, f: dict) -> str:
    """For sale now: this version first, then other versions and 'possibly this card', each folded away."""
    from .card_market import THIS_CARD
    from .ui_listing import badge, verdict, _left
    EM = lambda v, sign=False: M(v)
    lots = [r for r in market if r["lot"]]
    plat, grd, typ = f.get("platform", ""), f.get("graded", ""), f.get("type", "")
    rows_in = []
    for r in market:
        if r["lot"]:
            continue
        graded = bool(r["grade_company"] or GRADED.search(r["title"] or ""))
        auction = r["kind"] in ("bid", "auction")
        if (plat and r["site"] != plat) or (grd == "raw" and graded) or (grd == "graded" and not graded) \
                or (typ == "bin" and auction) or (typ == "auction" and not auction):
            continue
        rows_in.append((r, graded, auction))
    sure = [x for x in rows_in if x[0]["card_prob"] >= THIS_CARD]
    this = [x for x in sure if x[0]["printing_id"] == chosen]
    other = [x for x in sure if x[0]["printing_id"] != chosen]
    maybe = [x for x in rows_in if x[0]["card_prob"] < THIS_CARD]

    def tr(r, graded, auction):
        v = verdict(r["payload"], r["status"], EM)
        pid = r["printing_id"]
        what = _vlabel(by_id[pid]) if pid in by_id else " · ".join((r["label"] or "").split(" · ")[:3])
        unsure = "" if r["payload"].get("printing_id") else " · version not certain"
        cond = " ".join(x for x in (r["grade_company"], r["grade"]) if x) or ("Graded" if graded else "Ungraded")
        ship = r["shipping"]
        from .ui_listing import item_id_of, pair_art
        from .photo_match import apply as photo_apply
        iid = item_id_of(r["key"], r["payload"])
        shown = photo_apply(r["payload"], r["key"])
        pic = pair_art(r["key"], shown, {iid: 1} if iid else None)
        from .ui_icons import icon
        site_ic = icon(r["site"], f'Listed on {SITE_NAME.get(r["site"], "another site")}') if r["site"] in ("ebay", "yahoo", "mercari") else ""
        return (f'<tr><td>{pic}</td>'
                f'<td>{site_ic} <a href="/opp/{quote(r["key"], safe="")}">{e(r["title"])}</a><br><span class="muted small">{e(what)}{unsure} · {e(cond)}</span></td>'
                f'<td class="n">{e(charts.fmt(float(r["price"]), r["currency"]))}</td>'
                f'<td class="n">{e(charts.fmt(float(ship), r["currency"])) if ship not in (None, "", "0") else "—"}</td>'
                f'<td class="n"><b>{M(r["all_in"])}</b></td><td>{"Auction" if auction else "Buy it now"}<br><span class="muted small">'
                f'{e(_left(r["payload"].get("ends_at"), now))}</span></td><td>{badge(v)}</td>'
                + (f'<td><a href="{e(r["url"])}" target="_blank" rel="noreferrer noopener">open ↗</a></td>' if (r["url"] or "").startswith("http") else "<td></td>")
                + '</tr>')

    def table(items):
        head = ('<tr><th><span class="sr">Photo</span></th><th>Listing</th><th class="n">Price</th><th class="n">Shipping</th><th class="n">Total to you</th><th>Type</th>'
                '<th>Our take</th><th></th></tr>')
        return (f'<div class="wrap"><table class="mkt">{head}{"".join(tr(*x) for x in items[:60])}</table></div>'
                + (f'<p class="muted small">Showing the cheapest 60 of {len(items)}.</p>' if len(items) > 60 else ""))

    def chip(param, val, text):
        q = {**{k: x for k, x in f.items() if x}, param: val}
        q = {k: x for k, x in q.items() if x}
        return f'<a class="fchip{" on" if f.get(param, "") == val else ""}" href="{e(card_href(passcode))}?{e(urlencode(q))}#market">{e(text)}</a>'
    sites = sorted({r["site"] for r, _, _ in rows_in} | {r["site"] for r in market if not r["lot"]})
    chips = ('<div class="fchips">' + chip("platform", "", "All sites") + "".join(chip("platform", s_, SITE_NAME.get(s_, "Other")) for s_ in sites)
             + '<span class="sep"></span>' + chip("graded", "", "Any condition") + chip("graded", "raw", "Ungraded") + chip("graded", "graded", "Graded")
             + '<span class="sep"></span>' + chip("type", "", "Any type") + chip("type", "bin", "Buy it now") + chip("type", "auction", "Auction") + '</div>')
    return (f'<section id="market"><h2>For sale now</h2><p class="why small">Cheapest first. "Total to you" adds shipping and your buying costs.</p>'
            + chips
            + (table(this) if this else '<p class="empty">Nothing of this version is for sale right now.</p>')
            + (f'<details class="dsec"><summary>Other versions of this card ({len(other)})</summary>{table(other)}</details>' if other else "")
            + (f'<details class="dsec"><summary>Possibly this card ({len(maybe)})</summary><p class="why small">The title could also be '
               f'another card. Open one to see.</p>{table(maybe)}</details>' if maybe else "")
            + (f'<p class="muted small">Also in {len(lots)} lot{"s" if len(lots) != 1 else ""} (several cards sold together). '
               '<a href="/opportunities?t=lots">See lots</a></p>' if lots else "")
            + '</section>')


def _sold_panel(st, variants, chosen, M) -> str:
    """Real sales of this card, newest first: this version, then everything else folded away."""
    from .card_market import site_of
    ids = [v.id for v in variants]
    by_id = {v.id: v for v in variants}
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
        return '<section><h2>Recently sold</h2><p class="empty">No sales of this card seen yet.</p></section>'
    how = {"ACCEPTED_OFFER": " · best offer accepted"}

    def li(o, show_version):
        cond = (o.grade_company + " " + (o.grade or "")).strip() if o.grade_company else "Ungraded"
        ver = ""
        if show_version:
            ver = " · " + (_vlabel(by_id[o.printing_candidates[0]]) if len(o.printing_candidates) == 1 and o.printing_candidates[0] in by_id
                           else "version not known")
        d = o.event_time.date()
        return (f'<li><span class="plat {e(site_of(o.source))}">{e(SITE_NAME.get(site_of(o.source), "Other"))}</span>'
                f'<span>{d:%b} {d.day}, {d.year} · {e(cond)}{e(how.get(o.evidence_type.value, ""))}{e(ver)}</span>'
                f'<span class="sold-p">{e(charts.fmt(float(o.original_amount), o.currency))}</span></li>')
    mine = [o for o in obs if o.printing_candidates == (chosen,)]
    rest = [o for o in obs if o.printing_candidates != (chosen,)]
    return ('<section><h2>Recently sold</h2>'
            + ('<ul class="soldlist">' + "".join(li(o, False) for o in mine[:15]) + '</ul>' if mine else '<p class="empty">No sales of this version seen yet.</p>')
            + (f'<details class="dsec"><summary>Other versions, or version not known ({len(rest)})</summary><ul class="soldlist">'
               + "".join(li(o, True) for o in rest[:30]) + '</ul></details>' if rest else "")
            + '</section>')


def _versions_table(passcode, all_v, chosen, value, listed, M) -> str:
    rows = []
    for v in sorted(all_v, key=lambda v: (-len(listed.get(v.id, [])), _vlabel(v))):
        est = getattr(value.get(v.id), "estimate", None)
        n = len(getattr(value.get(v.id), "sales", []) or [])
        here = listed.get(v.id, [])
        cheapest = min((r["all_in"] for r in here if r["all_in"] is not None), default=None)
        rows.append(f'<tr{" class=on" if v.id == chosen else ""}><td><a href="{e(card_href(passcode))}?{e(urlencode({"v": v.id}))}">{e(v.set_code or "—")}</a></td>'
                    f'<td>{e(v.product_name or "")}</td><td>{e(v.rarity)}</td><td>{e(v.edition or "")}</td>'
                    f'<td>{"Japanese" if v.region.value == "JP_OCG" else "English"}</td>'
                    f'<td class="n">{n or "—"}</td><td class="n">{M(est.mid) if est else "—"}</td>'
                    f'<td class="n">{len(here) or "—"}</td><td class="n">{M(cheapest) if cheapest is not None else "—"}</td></tr>')
    return (f'<section><details class="dsec"><summary>All {len(all_v)} versions of this card</summary><div class="wrap"><table class="prints">'
            '<tr><th>Set</th><th>Product</th><th>Rarity</th><th>Edition</th><th>Language</th><th class="n">Sales</th><th class="n">Sells for</th>'
            '<th class="n">For sale</th><th class="n">Cheapest</th></tr>' + "".join(rows) + '</table></div></details></section>')


def _sale_form(passcode: str, variants, chosen, token: str, now: datetime) -> str:
    opts = "".join(f'<option value="{e(v.id)}"{" selected" if v.id == chosen else ""}>{e(_vlabel(v))}</option>' for v in variants)
    return (f'<section id="record"><details><summary>Know a sale of this card? Add it</summary>'
            '<p class="why">Your own sale, a private deal, or one you saw at a show. It counts like any other sale.</p>'
            f'<form method="post" action="{e(card_href(passcode))}/sale" class="inline sale"><input type="hidden" name="t" value="{e(token)}">'
            f'<label>Version<select name="printing" required>{opts}</select></label>'
            '<label>Price<input name="amount" inputmode="decimal" required size="9"></label>'
            '<label>Currency<select name="currency"><option>USD</option><option>TWD</option><option>JPY</option><option>EUR</option>'
            '<option>GBP</option></select></label>'
            f'<label>Date<input type="date" name="date" value="{now.date().isoformat()}" required></label>'
            '<label>How it sold<select name="kind"><option value="transaction">Sold</option><option value="accepted_offer">Best offer accepted'
            '</option></select></label>'
            '<label>Condition<select name="grade_company"><option value="">Ungraded</option><option>PSA</option><option>BGS</option><option>CGC</option>'
            '<option>ARS</option></select></label><label>Grade<input name="grade" size="4"></label>'
            '<label>Link<input name="url" type="url" placeholder="https://…" size="28"></label>'
            '<button>Add sale</button></form></details></section>')


def record_sale(st, catalog, passcode: str, form: dict, now: datetime) -> str:
    """A sale you witnessed, as one write-once record for exactly the printing you picked."""
    import uuid
    from .models import EvidenceType, MarketObservation
    pid = form.get("printing", "")
    base = pid.split("|")[0]
    if passcode.startswith("pkm-"):
        # Pokémon: the version must belong to exactly this card (set + number).
        if catalog is None or getattr(catalog, "game", "") != "pkm" or catalog.by_id.get(pid) is None or not pid.endswith("@" + passcode):
            raise ValueError("pick a version of this card")
    elif catalog is None or not any(p.id == base and p.artwork_id == f"art:pw{passcode}" for p in catalog.printings):
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


def character_history(st, name: str, variants: list, now: datetime, cur: str, home: str, book, guide) -> dict:
    """One series for the whole character: every sale, ask and guide of every card it owns, on one timeline.

    Also returns `live`: the latest ask per version, keyed by printing id, so the page can name what is on sale."""
    from .history import Converter, cached_build, buckets, summarize
    h = cached_build(st, "pkm-char:" + name, variants, now,
                     Converter(st, home, cur, getattr(book, "fx", None)), guide_fn=guide)
    sales = sorted((s for ser in h["series"] for s in ser["sales"]), key=lambda s: s["t"])
    asks = sorted((a for ser in h["series"] for a in ser["asks"]), key=lambda a: (a["d"], a["low"]))
    guides = sorted((g for ser in h["series"] for g in ser["guide"]), key=lambda g: g["d"])
    pts = [(datetime.fromisoformat(s["t"]), s["p"]) for s in sales]
    live = {}
    for ser in h["series"]:
        if ser["asks"]:
            live[ser["printing_id"]] = max(ser["asks"], key=lambda a: a["d"])
    return {"currency": h["currency"], "now": h["now"], "default": "all", "live": live, "series": [{
        "key": "all", "printing_id": "", "label": "All cards", "grade": "Raw", "sales": sales, "asks": asks, "guide": guides,
        "week": buckets(pts, "week"), "month": buckets(pts, "month"),
        "summary": {**summarize(pts, now), "last": sales[-1] if sales else None, "n": len(sales)}}]}


def _rank_svg(pts: list[dict], cur: str, w: int = 920, h: int = 260, fs: int = 13, compact: bool = False) -> str:
    """Each card version as one dot, cheapest on the left, on a log price axis; a live ask hangs off its dot.

    No bins and no invented averages: every dot is a real version and its real guide price, so the shape of the
    character's catalogue is visible as it is. A line below a dot is a version listed under its guide right now.
    Two renders are made (wide and narrow) because SVG text scales with the viewBox; CSS swaps them at 700px.
    """
    import math
    pts = sorted((p for p in pts if p.get("v") and p["v"] > 0), key=lambda p: p["v"])
    n = len(pts)
    if n < 3:
        return ""
    lo, hi = pts[0]["v"], pts[-1]["v"]
    if hi <= lo:
        return ""
    L, R, T, B = (44, 8, 26, 34) if not compact else (40, 8, 24, 32)
    base = h - B
    a, b = math.log(lo), math.log(hi)

    def Y(v):
        return T + (base - T) * (1 - (math.log(min(max(v, lo), hi)) - a) / (b - a))

    def X(i):
        return L + (w - L - R) * i / (n - 1)

    out = [f'<svg class="ladsvg" viewBox="0 0 {w} {h}" style="font-size:{fs}px" role="img" '
           f'aria-label="Every card version from cheapest to priciest, log price scale">']
    out.append(f'<text x="0" y="12" class="tlab">{n} card versions, cheapest on the left</text>')
    ticks, p = [], 0
    while p < 9:
        for m in (1, 3):
            t = float(m * 10 ** p)
            if lo <= t <= hi:
                ticks.append(t)
        p += 1
    if compact and len(ticks) > 4:
        ticks = ticks[::2]
    for t in ticks:
        y = Y(t)
        out.append(f'<line x1="{L}" x2="{w - R}" y1="{y:.1f}" y2="{y:.1f}" class="lagrid"/>')
        out.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" class="tlab">'
                   f'{charts.fmt(t, cur).replace(".00", "")}</text>')
    out.append(f'<line x1="{L}" x2="{w - R}" y1="{base}" y2="{base}" class="laaxis"/>')
    r = 3.4 if not compact else 2.9
    for i, q in enumerate(pts):
        x, y = X(i), Y(q["v"])
        tip = f'{q["name"]}: guide {charts.fmt(q["v"], cur)}'
        if q.get("ask"):
            tip += f', lowest live ask {charts.fmt(q["ask"], cur)}'
            if q["ask"] < q["v"]:
                ya = Y(q["ask"])
                out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y:.1f}" y2="{ya:.1f}" class="askd"/>')
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" class="ladot"><title>{e(tip)}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def view_character(st, idx, species: str, token: str, now: datetime, cur: str, home: str, book) -> str:
    """One character: what each of its cards costs, what is on sale now, then the cards themselves."""
    from .character import cards_of, slug
    from .games import card_href
    from .history import Converter
    from .zone import book as sprites
    cat = idx.cat
    conv = Converter(st, home, cur, getattr(book, "fx", None))

    def money(usd):
        try:
            v = conv(usd, "USD", now, fetch=False)
        except Exception:
            v = None
        return float(v if v is not None else usd)

    ids = cards_of(cat, species)
    variants = [v for cid in ids for v in cat.versions(cid)]
    tiles = []
    priced = []
    where_of = {}
    gdays = []
    for cid in ids:
        row = cat._cards[cid]
        key = "pkm-" + cid
        s = cat.set(row[3]) or {}
        best = None
        for v in cat.versions(cid):
            label = v.edition or "Standard"
            where = f'{row[1]} · {s.get("name") or ""} {row[4]} · {label}'
            where_of[v.id] = where
            g = cat.guide(v.id)
            if not g:
                continue
            gdays.append(str(g[1])[:10])
            val = money(g[0])
            priced.append({"v": val, "name": where, "id": v.id, "ask": None})
            if best is None or val < best[0]:
                best = (val, label)
        if len(tiles) < 240:
            price = (f'<span class="ct-from">from </span>' if len(cat.versions(cid)) > 1 else "") + e(charts.fmt(best[0], cur)) \
                if best else '<span class="ct-np">no price yet</span>'
            tiles.append(f'<a class="ztile" href="{e(card_href(key))}">{img(key, "small", row[1])}'
                         f'<span class="ct-price">{price}</span></a>')
    hx = character_history(st, slug(species), variants, now, cur, home, book, cat.guide_history)
    ser = hx["series"][0]
    live = hx["live"]
    latest_day = max((a["d"] for a in live.values()), default=None)
    if latest_day:
        live = {vid: a for vid, a in live.items() if a["d"] == latest_day}
    months = ser["month"][-18:]
    n_sales = len(ser["sales"])
    priced.sort(key=lambda p: p["v"])
    guide_of = {p["id"]: p["v"] for p in priced}
    for p in priced:
        a = live.get(p["id"])
        if a:
            p["ask"] = a["low"]
    n_buy = sum(a.get("n") or 0 for a in live.values())
    deals = sorted(({"pct": (a["low"] - guide_of[vid]) / guide_of[vid] * 100, "name": where_of.get(vid, vid),
                     "ask": a["low"], "guide": guide_of[vid]}
                    for vid, a in live.items() if vid in guide_of and guide_of[vid] and a.get("low")),
                   key=lambda d: d["pct"])
    under = [d for d in deals if d["pct"] < 0]
    fday = max(gdays) if gdays else None
    first_day = min(gdays) if gdays else None
    if priced:
        lo_p, hi_p = priced[0]["v"], priced[-1]["v"]
        bits = [f'<b>{len(priced)}</b> card versions priced from <b>{e(charts.fmt(lo_p, cur))}</b> to '
                f'<b>{e(charts.fmt(hi_p, cur))}</b>' + (f' — {hi_p / lo_p:,.0f}× apart' if lo_p and hi_p / lo_p >= 10 else "") + '.']
        if n_buy:
            bits.append(f'<b>{n_buy}</b> listing{"s" if n_buy != 1 else ""} across <b>{len(live)}</b> cards in the '
                        f'latest listing snapshot ({e(latest_day)}).')
        else:
            bits.append('Nothing is listed right now, so the guide prices are the only prices on record.')
        if under:
            d0 = under[0]
            bits.append(f'<b>{len(under)}</b> version{"s" if len(under) != 1 else ""} sit'
                        f'{"s" if len(under) == 1 else ""} under guide; the deepest is <b>{abs(d0["pct"]):.0f}%</b> under — '
                        f'<b>{e(d0["name"])}</b> at <b>{e(charts.fmt(d0["ask"], cur))}</b> against a '
                        f'<b>{e(charts.fmt(d0["guide"], cur))}</b> guide.')
        elif deals:
            bits.append('Everything listed asks at or above its guide right now.')
        lead = " ".join(bits)
        chart = (f'<div class="idxchart"><span class="lad-d">{_rank_svg(priced, cur)}</span>'
                 f'<span class="lad-m">{_rank_svg(priced, cur, w=400, h=230, fs=11, compact=True)}</span></div>'
                 f'<div class="idxleg"><span><i class="kdot"></i>Guide price, one dot per card version</span>'
                 f'<span><i class="kask"></i>A version listed under its guide, hanging by the gap</span></div>')
    else:
        lead = 'No guide price exists for any card of this character yet.'
        chart = ""
    perf = []
    if fday:
        perf.append(f'Guides are TCGplayer market prices, latest snapshot <b>{e(fday)}</b>; they are not sales.')
    if priced:
        perf.append(f'Cheapest version: <b>{e(priced[0]["name"])}</b> at <b>{e(charts.fmt(priced[0]["v"], cur))}</b>. '
                    f'Priciest: <b>{e(priced[-1]["name"])}</b> at <b>{e(charts.fmt(priced[-1]["v"], cur))}</b>. '
                    'Different cards; the low price does not buy the high one.')
    if under:
        perf.append(f'<b>{len(under)}</b> versions are listed under guide right now. The table on the right is the shopping list.')
    elif deals:
        perf.append('No version is listed under guide right now; every ask is at or above its guide.')
    perf.append(f'<b>{n_sales}</b> real sale{"s" if n_sales != 1 else ""} recorded across these cards.'
                if n_sales else 'No real sale of any of these cards is recorded yet.')
    stats = (f'<div class="chero-stats">'
             f'<div><small>Cards</small><b>{len(ids)}</b></div>'
             f'<div><small>Versions priced</small><b>{len(priced)}</b></div>'
             f'<div><small>Priciest</small><b>{e(charts.fmt(priced[-1]["v"], cur)) if priced else "—"}</b></div>'
             f'<div><small>Listed now</small><b>{n_buy}</b></div>'
             f'<div><small>Under guide</small><b>{len(under)}</b></div>'
             f'<div><small>Sales recorded</small><b>{n_sales}</b></div></div>')
    if deals:
        rows = "".join(
            f'<tr><td><span class="dname">{e(d["name"])}</span></td>'
            f'<td class="n">{e(charts.fmt(d["ask"], cur))}</td><td class="n gcol">{e(charts.fmt(d["guide"], cur))}</td>'
            f'<td class="n"><span class="{"dunder" if d["pct"] < 0 else "dover"}">{d["pct"]:+.0f}%</span></td></tr>'
            for d in deals[:8])
        more = f'<p class="why small">Plus {len(deals) - 8} more versions listed.</p>' if len(deals) > 8 else ""
        board = (f'<h3>On sale right now</h3><table class="dbord"><thead><tr><th>Card version</th><th class="n">Live ask</th>'
                 f'<th class="n gcol">Guide</th><th class="n">vs guide</th></tr></thead><tbody>{rows}</tbody></table>{more}'
                 f'<p class="why small">From the {e(latest_day)} listing snapshot: lowest live listing of each version '
                 'against its guide price; negative means listed under it. A gap past about 60% usually means the '
                 'listing is a different printing — open the card first.</p>')
        facts = (f'<div class="chcols"><div><h3>What the numbers say</h3><ul class="chfacts">'
                 + "".join(f"<li>{p}</li>" for p in perf) + f'</ul></div><div>{board}</div></div>')
    else:
        facts = ('<h3>What the numbers say</h3><ul class="chfacts">' + "".join(f"<li>{p}</li>" for p in perf) + '</ul>')
    foot = ('<p class="why small">Guide prices, not sales. No version has a second saved guide yet, so no price moves '
            'are shown.</p>')
    vmax = max((b["n"] for b in months), default=0)
    if months and vmax:
        bars = "".join(
            f'<div class="vcol"><div class="vbar" style="height:{max(4, round(b["n"] / vmax * 100))}%" '
            f'title="{e(b["start"][:7])}: {b["n"]} sale{"s" if b["n"] != 1 else ""}"></div>'
            f'<small>{e(b["start"][5:7])}<br>{e(b["start"][2:4])}</small></div>' for b in months)
        volbody = (f'<p class="why small">Real sales per month across every card of {e(species)}, last {len(months)} months. '
                   f'Busiest month: {vmax}.</p><div class="vol">{bars}</div>')
    else:
        volbody = ('<p class="why small">No real sale of any of these cards is recorded yet, so there is no monthly '
                   'volume to draw. Guides are not sales.</p>')
    dex = sprites().dex(species)
    portrait = (f'<img class="charport" src="/pkm/icon/{dex}.png" alt="{e(species)}" width="360" height="360" '
                f'decoding="async">' if dex else "")
    return ('<style>main.page{max-width:min(95vw,1500px)}</style>'
            f'<section class="chero">{portrait}<div class="chero-txt">'
            f'<p class="chero-kick">{len(variants)} card versions tracked'
            + (f' · guide history since {e(f"{datetime.fromisoformat(first_day):%b %Y}")}' if first_day else "") + '</p>'
            f'<h1>{e(species)}</h1>{stats}</div></section>'
            f'<section class="char-analyse"><h2>How {e(species)} is performing</h2>'
            f'<p class="chlead">{lead}</p>{chart}{facts}'
            f'<h3>Sales volume</h3>{volbody}{foot}</section>'
            f'<h2 class="cghead">Every card of {e(species)}</h2>'
            f'<div class="cgrid2">{"".join(tiles) or "<p class=empty>No cards of this character.</p>"}</div>')


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
section.hero{text-align:center;padding:46px 12px 26px;max-width:760px;margin:0 auto;background:none;border:0;box-shadow:none}
section.hero.slim{padding:26px 12px 12px;box-shadow:none}.slimnote{max-width:760px;margin:0 auto 6px;font-size:13px}
.soldlist{list-style:none;padding:0;margin:0;display:grid;gap:8px}.soldlist li{display:grid;grid-template-columns:40px 1fr auto;gap:12px;align-items:center;
background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:8px 12px}.soldlist .card{width:40px;height:58px}
.sold-p{font-weight:700;font-variant-numeric:tabular-nums}
.hero h1{font-size:clamp(28px,5vw,40px);letter-spacing:-.02em;margin:0 0 6px;display:block}.hero>p{color:var(--fg2);font-size:16px;margin:0 0 22px}
.hero .try{font-size:13px;color:var(--muted);margin:14px 0 0}.hero .try a{color:var(--fg2)}
.charpage{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:28px;align-items:start}
.char-side{position:sticky;top:74px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px}
.char-side h2{margin:0 0 6px;font-size:16px}.char-side label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted);margin:0 0 8px}
.char-side select,.char-side input{font-size:14px;color:var(--fg)}
.char-move{font-size:13px;color:var(--fg2);margin:0 0 6px}
section.char-analyse{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:22px 24px;margin:0 0 26px;box-shadow:var(--shadow)}
section.char-analyse h2{margin:0 0 6px;font-size:clamp(22px,2.4vw,30px);letter-spacing:-.02em;font-weight:800}
section.char-analyse h3{margin:20px 0 8px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.chlead{margin:0 0 10px;font-size:15px;color:var(--fg2);max-width:70ch}
.chlead b{color:var(--fg)}
.chero{position:relative;display:flex;align-items:center;gap:clamp(16px,4vw,56px);margin:0 0 26px;padding:clamp(18px,3.4vw,40px) clamp(16px,3vw,48px);
border-radius:26px;border:1px solid var(--line);overflow:hidden;
background:radial-gradient(120% 150% at 8% 0%,color-mix(in srgb,var(--s3) 16%,transparent),transparent 58%),
radial-gradient(90% 120% at 100% 100%,color-mix(in srgb,var(--s2) 10%,transparent),transparent 55%),var(--surface);
box-shadow:var(--shadow-hi)}
.chero .charport{width:clamp(150px,24vw,340px);height:auto;flex:none;object-fit:contain;
filter:drop-shadow(0 26px 34px rgba(35,55,45,.24)) drop-shadow(0 4px 8px rgba(35,55,45,.12))}
.chero-txt{min-width:0}
.chero-kick{margin:0 0 6px;font-size:13px;letter-spacing:.02em;color:var(--muted)}
.chero h1{margin:0;font-size:clamp(46px,8.4vw,110px);line-height:.94;letter-spacing:-.045em;font-weight:900;color:var(--fg)}
.chero-stats{display:flex;flex-wrap:wrap;gap:10px 26px;margin:20px 0 0}
.chero-stats>div{display:flex;flex-direction:column;min-width:86px}
.chero-stats small{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.chero-stats b{font-size:clamp(19px,2vw,27px);font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.chero-stats b.mv-up,.chero-stats b.mv-down{font-size:clamp(19px,2vw,27px)}
.idxchart{margin:14px 0 2px;border:1px solid var(--line);border-radius:16px;padding:14px 12px 6px;max-width:1080px;background:linear-gradient(180deg,color-mix(in srgb,var(--soft) 60%,transparent),transparent 70%)}
.lad-d{display:block}.lad-m{display:none}
@media(max-width:700px){.lad-d{display:none}.lad-m{display:block}}
.ladsvg{display:block;width:100%;height:auto;overflow:visible}
.ladsvg .laaxis{stroke:var(--axis);stroke-width:1.4}
.ladsvg .lagrid{stroke:var(--line);stroke-width:1;stroke-dasharray:2 4;opacity:.7}
.ladsvg .tlab{fill:var(--muted);font-size:1em}
.ladsvg .ladot{fill:var(--fg2);fill-opacity:.55}
.ladsvg .ladot:hover{fill:var(--fg);fill-opacity:1}
.ladsvg .askd{stroke:var(--teal);stroke-width:2;stroke-linecap:round}
.ladsvg .askp{stroke:var(--fg2);stroke-width:1.4;stroke-opacity:.5;stroke-linecap:round}
.idxleg{display:flex;flex-wrap:wrap;gap:16px;margin:6px 0 2px;font-size:12px;color:var(--fg2)}
.idxleg i{display:inline-block;vertical-align:middle;margin-right:6px}
.idxleg .kdot{width:10px;height:10px;border-radius:50%;background:var(--fg2);opacity:.6}
.idxleg .kask{width:3px;height:14px;border-radius:2px;background:var(--teal)}
.dbord{width:100%;border-collapse:collapse;font-size:13px}
.dbord th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;
padding:0 8px 6px 0;border-bottom:1px solid var(--line);white-space:nowrap}
.dbord td{padding:7px 8px 7px 0;border-bottom:1px solid var(--line);vertical-align:middle}
.dbord td.n,.dbord th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.dbord .dname{display:block;max-width:330px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg)}
.dbord .dunder{color:var(--teal);font-weight:700}.dbord .dover{color:var(--fg2)}
.dbord tr:last-child td{border-bottom:0}
@media(max-width:700px){.dbord .gcol{display:none}.dbord .dname{max-width:min(52vw,260px)}}
.chcols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:26px;margin:6px 0 0}
@media(max-width:820px){.chcols{grid-template-columns:1fr}.chero{flex-direction:column;align-items:flex-start;gap:10px}}
.chfacts{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.chfacts li{font-size:14px;color:var(--fg2);line-height:1.45;padding-left:14px;position:relative}
.chfacts li:before{content:"";position:absolute;left:0;top:.55em;width:6px;height:6px;border-radius:50%;background:var(--s3)}
.chfacts b{color:var(--fg);font-variant-numeric:tabular-nums}
.mvlist{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.mvlist .mv{display:grid;grid-template-columns:64px minmax(0,1fr);gap:2px 10px;background:var(--soft);border:1px solid var(--line);
border-radius:12px;padding:9px 13px;font-size:13px;color:var(--fg2)}
.mvlist .mv b{grid-row:span 2;font-size:17px;font-weight:800;font-variant-numeric:tabular-nums;align-self:center}
.mvlist .mv span{color:var(--fg);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mvlist .mv small{color:var(--muted);font-variant-numeric:tabular-nums}
.mvlist .mv.up b{color:var(--good-text)}.mvlist .mv.down b{color:var(--crit)}
.vol{display:flex;align-items:flex-end;gap:6px;height:150px;margin:8px 0 6px}
.vcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%}
.vbar{width:100%;max-width:44px;background:var(--teal);opacity:.8;border-radius:6px 6px 0 0}
.vbar:hover{opacity:1}
.vcol small{font-size:10px;color:var(--muted);margin-top:4px;text-align:center;line-height:1.1}
.mv-up{color:var(--good-text)}.mv-down{color:var(--crit)}
.cghead{font-size:clamp(22px,2.4vw,30px);letter-spacing:-.02em;margin:6px 0 14px}
.cgrid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(215px,42%),1fr));gap:34px 22px}
.ztile{display:flex;flex-direction:column;align-items:center;gap:10px;text-decoration:none;color:var(--fg);background:none;border:0;padding:0}
.ztile .card{width:100%;margin:0;border-radius:4.5%/3.2%;box-shadow:0 12px 26px rgba(30,40,30,.18);
transition:transform .18s var(--ease),box-shadow .18s var(--ease)}
.ztile:hover .card{transform:translateY(-6px) scale(1.02);box-shadow:0 24px 46px rgba(30,40,30,.28)}
.ct-price{font-size:18px;font-weight:800;color:var(--fg);background:var(--surface);border:1px solid var(--line);
border-radius:999px;padding:4px 16px;box-shadow:var(--shadow);font-variant-numeric:tabular-nums}
.ct-from{font-size:12px;font-weight:600;color:var(--muted)}
.ct-np{font-size:12px;font-weight:600;color:var(--muted)}
@media(max-width:860px){.charpage{grid-template-columns:1fr}.char-side{position:static}}
.shelf{background:none;border:0;padding:0;margin:26px 0}.shelf-h{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 10px}
.shelf-h h2{font-size:19px;margin:0}.shelf-h a{font-size:13px}
.tiles-l{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(250px,100%),1fr));gap:12px}
@media(max-width:800px){.shell{grid-template-columns:minmax(0,1fr)!important}header{flex-wrap:wrap}header .hsearch{order:9;flex-basis:100%;max-width:none}
header .store{display:none}form.search.big input{font-size:16px;padding:12px 16px}form.search.big button{padding:0 16px}}
.tile-l{display:flex;gap:12px;padding:12px;background:var(--surface);border:1px solid var(--line);border-radius:12px;color:var(--fg);
text-decoration:none;transition:box-shadow .15s,transform .15s}.tile-l:hover{box-shadow:var(--shadow-hi);transform:translateY(-2px);border-color:var(--axis)}
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
.ctile{display:flex;flex-direction:column;gap:3px;padding:10px;background:var(--surface);border:1px solid var(--line);border-radius:12px;color:var(--fg);text-decoration:none;box-shadow:var(--shadow);transition:box-shadow .15s var(--ease),transform .15s var(--ease),border-color .15s var(--ease)}
.ctile:hover{box-shadow:var(--shadow-hi);transform:translateY(-2px);border-color:var(--axis)}.ctile .card{width:100%;margin:0 0 6px}
.ct-name{font-weight:600;line-height:1.25}.ct-ja{font-size:12px;color:var(--fg2)}.ct-meta{font-size:12px;color:var(--muted)}
.ct-why{font-size:11px;color:var(--accent)}.ct-mkt{font-size:12px;font-weight:650;color:var(--fg)}
.plat{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;border:1px solid var(--axis);white-space:nowrap}
.plat.ebay{color:#0064d2}.plat.yahoo{color:#b3261e}.plat.mercari{color:#d24a4a}table.mkt tr.maybe td{opacity:.85}
select{max-width:100%}.vpick{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}form.vpick label{display:flex;flex-direction:row;gap:8px;align-items:center;font-weight:600;min-width:0;max-width:100%;margin:0}form.vpick button{margin:0}.vpick select{min-width:0;width:auto;max-width:min(440px,62vw);font-weight:400;margin:0}table.prints tr.on td{background:var(--bg2,rgba(127,127,127,.08))}.pricebox b{display:block}.pricebox span{display:block}form.sale{flex-wrap:wrap}form.sale label{min-width:0;max-width:100%}form.sale label select{width:100%}.pfilter{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}#market .pfilter label{display:flex;flex-direction:row;gap:6px;align-items:center;min-width:0;max-width:100%;font-size:13px;color:var(--fg2);margin:0}#market .pfilter select{min-width:0;width:auto;max-width:min(520px,70vw);margin:0}#market .pfilter button{margin:0}
img.mthumb{width:44px;height:58px;object-fit:cover;border-radius:6px;background:var(--soft);display:block}.sr{position:absolute;left:-9999px}
.moves{display:grid;gap:8px;margin:10px 0}.move{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--surface)}.move p{margin:4px 0 0;font-size:13px}.mdmg{float:right;font-weight:700}.mtag{font-size:11px;color:var(--accent);margin-right:4px}img.card.pkm{border-radius:4.5%}
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
--axis:#3a3429;--soft:#1e1b16;--accent:#d4af6a;--good-text:#9ccf7e;--good:#6fae4f;--canvas:none}
:root[data-theme="vault"] .hero h1,:root[data-theme="vault"] .cname,:root[data-theme="vault"] .shelf-h h2{
font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:600;letter-spacing:0}
:root[data-theme="vault"] .hero h1{color:var(--accent);font-size:44px}
:root[data-theme="vault"] form.search button{background:var(--accent);border-color:var(--accent);color:#15130f}
:root[data-theme="vault"] .tile-l,:root[data-theme="vault"] .ctile{border-color:#2f2a21;background:linear-gradient(180deg,#18150f,#13110d)}
:root[data-theme="vault"] .tl-price{color:var(--accent)}
.hunzone{position:relative;margin:56px 0 72px;padding:clamp(20px,3vw,42px) clamp(18px,3vw,46px);
border:1px solid var(--line);border-radius:26px;overflow:hidden;
background:radial-gradient(110% 140% at 6% 0%,rgba(47,191,113,.07),transparent 55%),
radial-gradient(90% 130% at 100% 6%,rgba(230,162,60,.06),transparent 50%),
radial-gradient(120% 150% at 88% 100%,rgba(224,82,82,.05),transparent 55%),var(--surface);
box-shadow:var(--shadow-hi)}
.hun-head{display:flex;align-items:flex-end;justify-content:space-between;gap:22px;flex-wrap:wrap;margin:0 0 18px}
.hun-kick{margin:0 0 8px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.hun-h{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-weight:900;font-size:clamp(40px,6vw,72px);
line-height:.92;letter-spacing:-.045em;text-transform:uppercase;margin:0;color:var(--fg)}
.hun-stats{display:flex;flex-wrap:wrap;gap:8px;padding-bottom:6px}
.hun-stats a{display:flex;flex-direction:column;align-items:center;min-width:76px;padding:8px 13px 9px;border:1px solid var(--line);
border-radius:14px;background:var(--surface);text-decoration:none;transition:border-color .15s,transform .15s}
.hun-stats a:hover{transform:translateY(-2px)}
.hun-stats a.hsg{color:#2fbf71}.hun-stats a.hsa{color:#e6a23c}.hun-stats a.hsb{color:#e05252}.hun-stats a.hsn{color:var(--fg2)}
.hun-stats a.on{border-color:currentColor;box-shadow:inset 0 0 0 1px currentColor}
.hun-stats small{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}
.hun-stats b{font-size:21px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.hun-tools{display:flex;align-items:center;gap:16px;margin:0 0 22px;flex-wrap:wrap}
.hun-fresh{display:inline-flex;border:1px solid var(--axis);border-radius:999px;overflow:hidden;background:var(--surface)}
.hun-fresh a{padding:6px 15px;font-size:13px;color:var(--fg2);text-decoration:none}
.hun-fresh a.on{background:var(--fg);color:var(--surface)}
.hun-shown{color:var(--muted)}
.hun-all{margin-left:auto;font-size:14px;font-weight:600}
.hun-note{color:var(--muted);margin:24px 2px 0;line-height:1.5;max-width:88ch}
section.hero.hunt{padding:64px 12px 10px}
.huntitle{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-style:italic;font-weight:700;
font-size:clamp(34px,7vw,64px);letter-spacing:.01em;margin:0 0 30px;cursor:default}
.huntitle span{display:inline-block;white-space:pre;transition:transform .35s cubic-bezier(.2,1.8,.4,1),color .35s;transition-delay:calc(var(--i)*30ms)}
.huntitle:hover span{transform:translateY(-8px) rotate(-5deg);color:var(--accent)}
.hungrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:22px}
a.hun{position:relative;display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);border-radius:16px;
overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06);text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s}
a.hun:hover{transform:translateY(-4px);box-shadow:0 12px 28px rgba(0,0,0,.18)}
.laser{height:4px;width:100%;flex:none}
.l-good{background:linear-gradient(90deg,#2fbf71,#5ad79a);box-shadow:0 0 10px #2fbf7180}
.l-avg{background:linear-gradient(90deg,#e6a23c,#f0c078);box-shadow:0 0 10px #e6a23c80}
.l-bad{background:linear-gradient(90deg,#e05252,#f08080);box-shadow:0 0 10px #e0525280}
.l-none{background:var(--line)}
a.hun img.hun-img,a.hun span.hun-img{width:100%;height:230px;object-fit:cover;border-radius:0;background:var(--bg)}
a.hun span.card.hun-img,a.hun img.card.hun-img{object-fit:contain;padding:10px;box-sizing:border-box}
.hun-b{display:flex;flex-direction:column;gap:5px;padding:12px 14px 14px}
.hun-name{font-size:14px;font-weight:600;color:var(--fg);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.7em}
.hun-price{font-size:19px;font-weight:700;color:var(--accent)}
.hun-row{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.hun-meta{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);white-space:nowrap}
.mkt{display:inline-flex;align-items:center;justify-content:center;width:19px;height:19px;border-radius:50%;flex:none;
font:700 11px/1 "Helvetica Neue",Arial,sans-serif;color:#fff}
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
t=setTimeout(function(){if(q===last)return;last=q;fetch((location.pathname.indexOf('/pkm')===0?'/pkm':'')+'/api/suggest?q='+encodeURIComponent(q)).then(function(r){return r.json()})
.then(function(d){if(inp.value.trim()===q)render(d.results||[])}).catch(function(){})},110)});
inp.addEventListener('keydown',function(ev){if(box.hidden)return;if(ev.key==='ArrowDown'){ev.preventDefault();mark(Math.min(sel+1,items.length-1))}
else if(ev.key==='ArrowUp'){ev.preventDefault();mark(Math.max(sel-1,-1))}else if(ev.key==='Enter'&&sel>=0){ev.preventDefault();location.href=items[sel].href}
else if(ev.key==='Escape'){box.hidden=true}});
document.addEventListener('click',function(ev){if(!f.contains(ev.target))box.hidden=true})})})();
"""


def suggest_json(idx, q: str) -> bytes:
    return json.dumps({"results": idx.suggest(q, 8)}, ensure_ascii=False).encode()
