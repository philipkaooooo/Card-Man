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


def _markets(idx, game: str) -> str:
    """The catalogs and the character markets, on the home, not behind a listing."""
    if game == "pkm":
        from .character import slug
        who = ["Charizard", "Pikachu", "Mew", "Mewtwo", "Umbreon", "Gengar", "Rayquaza", "Lugia",
               "Blastoise", "Venusaur", "Darkrai", "Gardevoir"]
        links = "".join(f'<a class="fchip" href="/pkm/character/{e(slug(n))}">{e(n)}</a>' for n in who)
        n = len(idx.cat) if idx is not None and getattr(idx, "cat", None) is not None else 0
        count = f"{n:,} Pokémon cards are on this site. " if n else ""
        return ('<section><h2>Character markets</h2>'
                f'<p class="sub">{count}Open a character. Every card of it is listed with its own guide price. '
                'One card is never the price of another.</p>'
                f'<div class="fchips">{links}</div></section>')
    who = ["Blue-Eyes White Dragon", "Dark Magician", "Red-Eyes Black Dragon", "Exodia the Forbidden One", "Ash Blossom"]
    links = "".join(f'<a class="fchip" href="/search?q={e(n.replace(" ", "+"))}">{e(n)}</a>' for n in who)
    return ('<section><h2>Card markets</h2>'
            '<p class="sub">Every printing has its own guide price. Open the card, then pick the version. '
            'A guide is not a sale.</p>'
            f'<div class="fchips">{links}</div></section>')


def view_home(st, idx, now: datetime, cur: str, theme: str, home: str = "TWD", assumed_costs: bool = True, EM=None, game: str = "ygo") -> str:
    """Search, then the decisions: the rail with live counts, the top Buys, then the 2x Checks. Nothing else."""
    from .ui_listing import BUY, CHECK, WATCH, decisions, row_html
    M = Money(st, home, cur, now)
    vault = theme == "vault"
    have = _tables(st)
    out = [('<section class="hero slim">'
            f'<h1>{"The Vault" if vault else "Find the exact card."}</h1>'
            + _search_box(big=True, game=game) +
            ('<p class="try">Try <a href="/search?q=Charizard+ex">Charizard ex</a> · <a href="/search?q=199%2F165">199/165</a> · '
             '<a href="/search?q=Pikachu+Base">Pikachu Base</a></p></section>' if game == "pkm" else
             '<p class="try">Try <a href="/search?q=Blue-Eyes+White+Dragon">Blue-Eyes White Dragon</a> · '
             '<a href="/search?q=%E9%9D%92%E7%9C%BC%E3%81%AE%E7%99%BD%E9%BE%8D">青眼の白龍</a> · <a href="/search?q=LOB-EN001">LOB-EN001</a></p></section>'))]
    if assumed_costs:
        out.append('<p class="note slimnote">"Worth paying up to" prices use <b>starter</b> costs. <a href="/settings">Set your real fees and shipping</a>.</p>')

    if "opportunity" in have and EM is not None:
        from .ui_decide import deal_card, first_seen, rail
        from .ui_listing import PASS, item_id_of, photo_counts
        items = decisions(st, EM, game)
        high = lambda d: (d.get("ask_home") or 0) >= float(VAULT_MIN_HOME) or re.search(r"\b(PSA|BGS|CGC|ARS|SGC)\s*\d", d.get("title") or "", re.I)
        if vault:
            items = [i for i in items if high(i[2])]
        counts = {c: sum(1 for i in items if i[3].code == c) for c in (BUY, CHECK, WATCH, PASS)}
        # A check with no sale price used to sort first, so the home opened on the wrong cards.
        rank = lambda i: (i[3].margin is None, -(i[3].margin or 0), -(i[2].get("priority") or 0))
        buy = sorted((i for i in items if i[3].code == BUY), key=rank)[:6]
        worth = [i for i in items if i[3].code == CHECK and "MISIDENTIFIED" not in (i[2].get("kinds") or [])
                 and (i[3].top or {}).get("basis") == "sales"]
        check = sorted(worth or (i for i in items if i[3].code == CHECK and "MISIDENTIFIED" not in (i[2].get("kinds") or [])), key=rank)[:6]
        ph = photo_counts(st, [item_id_of(k, d) for k, _s, d, _v in buy + check])
        seen = first_seen(st, [k for k, *_ in buy + check])
        cards_of = lambda group: "".join(deal_card(k, d, v, idx, EM, now, ph, seen.get(k)) for k, _s, d, v in group)
        out.append(rail(counts, None, lambda code, on: f"/opportunities?v={code}", compact=True))
        out.append('<div class="home-h"><h2>Top Buys</h2><a href="/opportunities?v=BUY">All Buys →</a></div>'
                   + (f'<div class="deals">{cards_of(buy)}</div>' if buy else
                      '<p class="empty">No Buy right now: we only say Buy after enough real sales of that exact card. '
                      'CardMan checks eBay every 15 minutes while it is open; you can also <a href="/import">add sales</a>.</p>'))
        out.append('<div class="home-h"><h2>2x Checks</h2><a href="/opportunities?v=CHECK">All 2x Checks →</a></div>'
                   + (f'<div class="deals">{cards_of(check)}</div>' if check else '<p class="empty">Nothing to double-check right now.</p>'))
        live_keys = None
        if "listing" in have:
            live_keys = {r[0] for r in st.db.execute("SELECT key FROM listing WHERE ended_at IS NULL")}
        live = [i for i in items if i[1] != "REJECTED" and (live_keys is None or i[0] in live_keys)]
        live.sort(key=lambda i: (i[3].code == "PASS", -(i[2].get("priority") or 0)))
        shown = live[:8]
        ph2 = photo_counts(st, [item_id_of(k, d) for k, _s, d, _v in shown])
        seen2 = first_seen(st, [k for k, *_ in shown])
        out.append('<div class="home-h"><h2>Live listings</h2><a href="/opportunities">All listings →</a></div>'
                   + (f'<div class="deals">{"".join(deal_card(k, d, v, idx, EM, now, ph2, seen2.get(k)) for k, _s, d, v in shown)}</div>'
                      f'<p class="muted small">{len(live):,} still up. The rest are on Listings, in the top bar.</p>' if shown else
                      '<p class="empty">No live listing for this game. Listings, in the top bar, is the full page.</p>'))
    else:
        out.append('<div class="home-h"><h2>Live listings</h2><a href="/opportunities">Listings →</a></div>'
                   '<p class="empty">None in this folder yet. Listings is the top-bar page. It fills when a check runs, or when you <a href="/import">add sales</a>.</p>')
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


def view_character(idx, species: str, token: str, now: datetime) -> str:
    """Every card of one character. Each keeps its own price. The right side records a sale of one exact version."""
    from .character import cards_of, slug
    from .games import card_href
    cat = idx.cat
    ids = cards_of(cat, species)
    tiles = []
    moves = []
    options = []
    priced = []
    for cid in ids[:80]:
        row = cat._cards[cid]
        key = "pkm-" + cid
        s = cat.set(row[3]) or {}
        versions = cat.versions(cid)
        lines = []
        for v in versions:
            g = cat.guide(v.id)
            label = v.edition or "Standard"
            if g:
                lines.append(f'{e(label)} · guide {e(charts.fmt(g[0], "USD"))} on {e(g[1])}')
                priced.append((g[0], f'{row[1]} · {s.get("name") or ""} {row[4]} · {label}'))
                hist = cat.guide_history(v.id)
                if len(hist) >= 2 and hist[0][1] != hist[-1][1]:
                    moves.append(f'{e(row[1])} {e(label)} ({e(s.get("name") or "")}): {e(charts.fmt(hist[0][1], "USD"))} on {e(hist[0][0])} → {e(charts.fmt(hist[-1][1], "USD"))} on {e(hist[-1][0])}. This card only.')
            options.append(f'<option value="{e(v.id)}">{e(row[1])} · {e(s.get("name") or "")} {e(row[4] or "")} · {e(label)}</option>')
        own = "<br>".join(lines) if lines else "No price of its own yet."
        tiles.append(f'<a class="ctile" href="{e(card_href(key))}">{img(key, "small", row[1])}'
                     f'<span class="ct-name">{e(row[1])}</span>'
                     f'<span class="ct-meta">{e(s.get("name") or "")} · {e(row[4] or "")}/{e(s.get("printed_total") or "")}</span>'
                     f'<span class="ct-meta">{e(row[6] or "")}</span>'
                     f'<span class="ct-mkt">{own}</span></a>')
    priced.sort()
    if len(priced) >= 2:
        lo, hi = priced[0], priced[-1]
        spread = (f'<p>The cheapest guide among these cards is {e(charts.fmt(lo[0], "USD"))} ({e(lo[1])}). '
                  f'The highest is {e(charts.fmt(hi[0], "USD"))} ({e(hi[1])}). '
                  'Those are different cards. The low one is not a price for the high one.</p>')
    else:
        spread = '<p>Not enough of these cards have their own guide price to compare.</p>'
    trend = "".join(f'<p class="char-move">{m}</p>' for m in moves[:12]) or '<p class="muted">No card of this character has two guide points yet, so there is no move to show.</p>'
    form = ""
    if token and options:
        form = (f'<form method="post" action="/pkm/character/sale" class="scanform">'
                f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="back" value="/pkm/character/{e(slug(species))}">'
                f'<label>Exact version<select name="printing" required>{"".join(options)}</select></label>'
                '<label>Price<input name="amount" inputmode="decimal" required size="9"></label>'
                '<label>Currency<select name="currency"><option>USD</option><option>TWD</option><option>JPY</option><option>EUR</option><option>GBP</option></select></label>'
                f'<label>Date<input type="date" name="date" value="{now.date().isoformat()}" required></label>'
                '<label>How it sold<select name="kind"><option value="transaction">Sold</option><option value="accepted_offer">Best offer accepted</option></select></label>'
                '<button>Add sale</button></form>')
    return (f'<div class="charpage"><div><h1>{e(species)}</h1>'
            f'<p class="sub">{len(ids)} card{"s" if len(ids) != 1 else ""}. Each one has its own price. Nothing on this page is a price for a different card.</p>'
            f'<div class="cgrid">{"".join(tiles) or "<p class=empty>No cards of this character.</p>"}</div>'
            f'<section><h2>How these cards move</h2>{trend}{spread}</section></div>'
            f'<aside class="char-side"><h2>Add a sale</h2><p class="why small">Pick the exact card and version. The sale counts for that version only.</p>{form}</aside></div>')


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
.charpage{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:28px;align-items:start}
.char-side{position:sticky;top:74px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px}
.char-side h2{margin:0 0 6px;font-size:16px}.char-side label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted);margin:0 0 8px}
.char-side select,.char-side input{font-size:14px;color:var(--fg)}
.char-move{font-size:13px;color:var(--fg2);margin:0 0 6px}
@media(max-width:860px){.charpage{grid-template-columns:1fr}.char-side{position:static}}
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
t=setTimeout(function(){if(q===last)return;last=q;fetch((location.pathname.indexOf('/pkm')===0?'/pkm':'')+'/api/suggest?q='+encodeURIComponent(q)).then(function(r){return r.json()})
.then(function(d){if(inp.value.trim()===q)render(d.results||[])}).catch(function(){})},110)});
inp.addEventListener('keydown',function(ev){if(box.hidden)return;if(ev.key==='ArrowDown'){ev.preventDefault();mark(Math.min(sel+1,items.length-1))}
else if(ev.key==='ArrowUp'){ev.preventDefault();mark(Math.max(sel-1,-1))}else if(ev.key==='Enter'&&sel>=0){ev.preventDefault();location.href=items[sel].href}
else if(ev.key==='Escape'){box.hidden=true}});
document.addEventListener('click',function(ev){if(!f.contains(ev.target))box.hidden=true})})})();
"""


def suggest_json(idx, q: str) -> bytes:
    return json.dumps({"results": idx.suggest(q, 8)}, ensure_ascii=False).encode()
