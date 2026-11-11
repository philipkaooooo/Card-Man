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


def _tile(key, title, price, currency, payload, M, *, ask_home=None, sub="") -> str:
    pc = _listing_art(payload)
    return (f'<a class="tile-l" href="/opp/{quote(key, safe="")}">{img(pc, "small", title)}'
            f'<span class="tl-body"><span class="tl-title">{e(title)}</span>'
            f'<span class="tl-price">{M(ask_home, original=(price, currency)) if ask_home is not None else e(charts.fmt(float(price), currency)) if price else "—"}</span>'
            f'<span class="tl-sub">{sub}</span>'
            f'{"" if pc else "<span class=tl-note>artwork shown only once the exact card is known</span>"}</span></a>')


def view_home(st, idx, now: datetime, cur: str, theme: str, home: str = "TWD") -> str:
    M = Money(st, home, cur, now)
    vault = theme == "vault"
    have = _tables(st)
    hero = ('<section class="hero">'
            f'<h1>{"The Vault" if vault else "Find the exact card."}</h1>'
            f'<p>{"High-value and graded listings only, with the evidence behind every number." if vault else "Every printing, every language, matched by passcode, with real sales behind the prices."}</p>'
            + _search_box(big=True) +
            '<p class="try">Try <a href="/search?q=Blue-Eyes+White+Dragon">Blue-Eyes White Dragon</a> · '
            '<a href="/search?q=%E9%9D%92%E7%9C%BC%E3%81%AE%E7%99%BD%E9%BE%8D">青眼の白龍</a> · <a href="/search?q=LOB-EN001">LOB-EN001</a> · '
            '<a href="/search?q=89631139">89631139</a></p></section>')
    out = [hero]

    if "opportunity" in have and "listing" in have:
        rows = st.db.execute(
            "SELECT o.key, o.status, o.kinds, o.payload_json, l.title, l.price, l.currency FROM opportunity o JOIN listing l ON l.key=o.key "
            "WHERE o.status IN ('OPEN','ESCALATED') ORDER BY (o.status='ESCALATED') DESC, o.priority DESC LIMIT 200").fetchall()
        tiles = []
        for key, status, kinds, pj, title, price, currency in rows:
            p = json.loads(pj)
            ah = p.get("ask_home")
            graded = bool(re.search(r"\b(PSA|BGS|CGC|ARS|SGC)\s*\d", title or "", re.I))
            if vault and not ((ah or 0) >= float(VAULT_MIN_HOME) or graded):
                continue
            sub = chips(json.loads(kinds or "[]")) + (' <span class="chip good">Escalated</span>' if status == "ESCALATED" else "")
            tiles.append(_tile(key, title, price, currency, p, M, ask_home=ah, sub=sub))
            if len(tiles) == 8:
                break
        out.append('<section class="shelf"><div class="shelf-h"><h2>Worth a look</h2><a href="/opportunities">All opportunities →</a></div>'
                   + (f'<div class="tiles-l">{"".join(tiles)}</div>' if tiles else
                      '<p class="empty">Nothing open right now. New listings are checked on every engine pass.</p>') + '</section>')

        recent = st.db.execute(
            "SELECT l.key, l.title, l.price, l.currency, o.payload_json FROM listing l LEFT JOIN opportunity o ON o.key=l.key "
            "WHERE l.ended_at IS NULL AND l.kind IN ('ask','bid','auction') ORDER BY l.first_seen DESC, l.rowid DESC LIMIT 200").fetchall()
        rt = []
        for key, title, price, currency, pj in recent:
            p = json.loads(pj) if pj else None
            ah = (p or {}).get("ask_home")
            if vault and not ((ah or 0) >= float(VAULT_MIN_HOME)):
                continue
            rt.append(_tile(key, title, price, currency, p, M, ask_home=ah))
            if len(rt) == 12:
                break
        out.append('<section class="shelf"><div class="shelf-h"><h2>Just listed</h2><span class="muted">newest first</span></div>'
                   + (f'<div class="tiles-l">{"".join(rt)}</div>' if rt else '<p class="empty">No live listings yet.</p>') + '</section>')

    if "market_signal" in have:
        # Price and volume moves first; at most three "seller clearing stock" notes, biggest first.
        sig = st.db.execute("SELECT kind, printing_id, at, detail_json FROM market_signal WHERE kind<>'SELLER_ACTIVITY' "
                            "ORDER BY at DESC LIMIT 6").fetchall()
        sellers = st.db.execute("SELECT kind, printing_id, at, detail_json FROM market_signal WHERE kind='SELLER_ACTIVITY' "
                                "ORDER BY at DESC LIMIT 60").fetchall()
        sellers.sort(key=lambda r: -(json.loads(r[3]).get("n") or 0))
        sig += sellers[:3]
        if sig:
            li = []
            for kind, pid, at, dj in sig:
                d = json.loads(dj)
                pc = passcode_of(pid)
                name = (idx.card(pc) or {}).get("name_en") if pc else None
                label = {"PRICE_SHIFT": "Price move", "VOLUME_SPIKE": "Selling fast", "SELLER_ACTIVITY": "Seller clearing stock"}.get(
                    kind, kind.replace("_", " ").title())
                li.append(f'<li><span class="chip info">{e(label)}</span> '
                          + (f'<a href="/card/{e(pc)}">{e(name)}</a> · ' if name else "") + f'{e(_move_text(d.get("text", "")))}</li>')
            out.append('<section class="shelf"><div class="shelf-h"><h2>Market moves</h2></div><ul class="moves">' + "".join(li) + '</ul></section>')

    n_cards = len(idx.cards) if idx is not None and getattr(idx, "_loaded", False) else None
    stats = []
    if n_cards:
        stats.append(f"<b>{n_cards:,}</b> cards")
        stats.append(f"<b>{sum(len(v) for v in idx._prints.values()):,}</b> printings")
    if "listing" in have:
        stats.append(f"<b>{st.db.execute('SELECT COUNT(*) FROM listing').fetchone()[0]:,}</b> listings tracked")
    sold = st.db.execute("SELECT COUNT(*) FROM observation WHERE synthetic=0 AND evidence_type IN "
                         "('MARKETPLACE_MARKED_SOLD','ACCEPTED_OFFER','INDEPENDENT_TRANSACTION')").fetchone()[0]
    stats.append(f"<b>{sold:,}</b> real sales recorded" + ("" if sold else ' · <a href="/import">import sold prices</a> to turn leads into deals'))
    out.append('<p class="statline">' + " · ".join(stats) + '</p>')
    return "".join(out)


def view_search(idx, q: str) -> str:
    hits = idx.search(q, 48) if q else []
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


def view_card(st, idx, passcode: str, now: datetime, cur: str, book=None, catalog=None, home: str = "TWD") -> str:
    c = idx.card(passcode)
    if not c:
        raise KeyError(f"no card with passcode {passcode}")
    M = Money(st, home, cur, now)
    prints = idx.printings(passcode)
    rows, lows = [], []
    from .identity import edition_variants
    all_v = [v for p in prints for v in (edition_variants(p, catalog) if catalog is not None else [p])]
    # Price only printings the store has any record of: one query instead of a valuation per printing.
    ids = [v.id for v in all_v]
    seen: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        seen |= {r[0] for r in st.db.execute(
            f"SELECT DISTINCT printing_id FROM observation_printing WHERE printing_id IN ({','.join('?' * len(chunk))})", chunk)}
    for v in all_v:
        if True:
            sales = asks = 0
            low = mid = None
            if book is not None and v.id in seen:
                try:
                    val = book.value(v.id)
                    sales, asks = len(val.sales), getattr(val, "competing", 0)
                    if val.estimate:
                        low, mid = val.estimate.low, val.estimate.mid
                        lows.append((low, v))
                except Exception:
                    pass
            scan = idx.printing_scan(passcode, v)
            region = {"EN_TCG": "EN", "JP_OCG": "JP"}.get(v.region.value, v.region.value)
            rows.append(
                f'<tr><td class="scan">{"<img src=/img/scan/" + quote(scan) + " alt=" + chr(34) + e(scan) + chr(34) + " loading=lazy>" if scan else ""}</td>'
                f'<td><span class="rg {region.lower()}">{e(region)}</span></td><td><code>{e(v.set_code or "—")}</code></td>'
                f'<td>{e(v.product_name or v.product_id)}</td><td>{e(v.rarity)}</td><td>{e(v.edition or "")}</td>'
                f'<td class="n">{sales or "—"}</td><td class="n">{M(low) if low is not None else "—"}</td>'
                f'<td class="n">{M(mid) if mid is not None else "—"}</td><td class="n">{asks or "—"}</td>'
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
        + '</div></div>'
        f'<section><h2>All {len(rows)} printings</h2><p class="why">From the identity catalog. Prices are only from recorded sales of that exact '
        'printing (and edition); live asks are copies for sale right now. A scan appears only when its file is that printing.</p>'
        '<div class="wrap"><table class="prints"><tr><th></th><th>Region</th><th>Set code</th><th>Product</th><th>Rarity</th><th>Edition</th>'
        '<th class="n">Sales</th><th class="n">Low</th><th class="n">Mid</th><th class="n">Live asks</th><th></th></tr>'
        + "".join(rows) + '</table></div></section>')


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
.ct-why{font-size:11px;color:var(--accent)}
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
