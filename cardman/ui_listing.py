"""One listing, one decision. The verdict is derived only from what the engine already computed; nothing here invents a
number. Detail (pricing, identity, costs, raw data) is one click away, closed by default.

Verdicts:
  BUY up to X       sold evidence backs a max-buy price at or above the all-in cost, and the identity is settled
  NEEDS YOUR CHECK  money depends on which printing it is (or the seller may have it wrong): a look at the card decides
  WATCH             something is worth following, but nothing proves a price yet (guide price only, an interesting lot)
  PASS              rejected, shelved, or loses money
Without real sold evidence there is never a BUY.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

BUY, CHECK, WATCH, PASS = "BUY", "CHECK", "WATCH", "PASS"
ORDER = {BUY: 0, CHECK: 1, WATCH: 2, PASS: 3}
LABEL = {BUY: "Buy", CHECK: "Check first", WATCH: "Watch", PASS: "Skip"}
SURE = {"High": "Sure", "Medium": "Fairly sure", "Low": "Not sure"}
CLS = {BUY: "good", CHECK: "warn", WATCH: "info", PASS: "crit"}
IDENTITY_KINDS = {"RIGHT_PRINT_ONLY", "MISIDENTIFIED", "HIDDEN_VARIANT"}


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


@dataclass
class Verdict:
    code: str
    headline: str
    reason: str
    confidence: str            # High / Medium / Low
    confidence_why: list[str]
    top: dict | None           # the scenario the numbers come from
    margin: float | None       # max buy minus all-in, home currency


def _sales_scenarios(d: dict) -> list[dict]:
    return [s for s in d.get("scenarios") or [] if s.get("basis") == "sales" and s.get("profit_mid") is not None]


def _n_candidates(d: dict) -> int:
    return len({s.get("printing_id") for s in d.get("scenarios") or []})


def _lot_size(d: dict) -> int | None:
    from .listing_parser import parse_listing_text
    return parse_listing_text(d.get("title") or "").lot_size


def verdict(d: dict, status: str, M) -> Verdict:
    """The decision in a friend's words. Same rules as before; only the wording is plain."""
    kinds = set(d.get("kinds") or [])
    sales = _sales_scenarios(d)
    share = float(d.get("priced_share") or 0)
    ncand = _n_candidates(d)
    resolved = bool(d.get("printing_id"))
    all_in = d.get("all_in_home")
    top = max(sales, key=lambda s: s.get("weight") or 0) if sales else None
    ref = next((s for s in sorted(d.get("scenarios") or [], key=lambda s: -(s.get("weight") or 0)) if s.get("basis") == "reference"), None)

    n = (top or {}).get("n_sales") or 0
    why = ["We know which version it is" if resolved else f"It could be one of {ncand} versions" if ncand else "We couldn't match a card",
           f"{n} recent sale{'s' if n != 1 else ''} to compare with" if top else "No recent sales to compare with"]
    level = "High" if resolved and n >= 6 and share >= 0.8 else "Medium" if share >= 0.5 and n >= 3 else "Low"
    margin = (top.get("max_buy") - all_in) if top and top.get("max_buy") is not None and all_in is not None else None
    version = lambda s: " · ".join(((s or {}).get("label") or "").split(" · ")[:2]) or "the right"

    def V(code, headline, reason):
        return Verdict(code, headline, reason, level, why, top or ref, margin)

    if status == "REJECTED":
        return V(PASS, "Skip", "Not a single card we can price (a proxy, a different game, or not what it says).")
    if "LOT" in kinds and "LOT_UNDERPRICED" not in kinds:
        size = _lot_size(d)
        what = f"A lot of {size} cards" if size else "A lot of several cards"
        if status in ("OPEN", "ESCALATED"):
            cue = ("the seller doesn't seem to know what's in it" if "SELLER_UNSURE" in kinds
                   else "it has older cards or a whole collection" if "COLLECTION" in kinds else "it might be worth a look")
            return V(WATCH, "Watch", f"{what}: we can't tell what each card is worth yet, but {cue}. Ask the seller for a card list or photos.")
        return V(PASS, "Skip", f"{what}, and nothing in it looks cheap.")
    if "LOT_UNDERPRICED" in kinds:
        return V(CHECK, "Check first", f"The cards in this lot usually sell for more than it costs (about {M(d.get('expected_profit'), True)} "
                 "profit after fees). Check the card list and condition.")
    if status == "SHELVED" and not (kinds & IDENTITY_KINDS):
        return V(PASS, "Skip", "Not cheaper than it usually sells for.")
    if top and top.get("max_buy") is not None and all_in is not None:
        if all_in <= top["max_buy"] and resolved and "UNDERPRICED" in kinds:
            return V(BUY, f"Buy (up to {M(top['max_buy'])})",
                     f"Cheaper than it usually sells for: {M(all_in)} to you, and it's worth paying up to {M(top['max_buy'])}. "
                     f"Based on {n} recent sale{'s' if n != 1 else ''}.")
        if all_in <= top["max_buy"]:
            return V(CHECK, "Check first", f"A good price if it's the {version(top)} version, but it could be one of {ncand} versions. "
                     "Check the photos first.")
        if not (kinds & IDENTITY_KINDS) and (d.get("best_profit") is None or d.get("best_profit") <= 0):
            return V(PASS, "Skip", f"Costs more than it usually sells for: {M(all_in)} to you, but it's only worth up to {M(top['max_buy'])}.")
    if kinds & IDENTITY_KINDS:
        if "MISIDENTIFIED" in kinds:
            f = next((f["text"] for f in d.get("findings") or [] if f.get("kind") in ("CODE_NAME_MISMATCH", "PHOTO_CONTRADICTS_SELLER")), None)
            return V(CHECK, "Check first", f or "The title and the card don't match. Check the photos to see which card it really is.")
        return V(CHECK, "Check first", "Can't tell which version this is, and they sell for different prices. Check the photos.")
    if "REFERENCE_GAP" in kinds or ref:
        return V(WATCH, "Watch", "Looks cheap next to the price guides, but we haven't seen enough real sales to be sure.")
    if "SELLER_UNSURE" in kinds:
        return V(WATCH, "Watch", "The seller isn't sure what card this is, so it may be priced too low. Not enough sales to be sure.")
    if status in ("OPEN", "ESCALATED"):
        return V(WATCH, "Watch", "Might be interesting, but we haven't seen enough sales to know what it's worth.")
    return V(PASS, "Skip", "Not cheaper than it usually sells for.")


def badge(v: Verdict, big: bool = False) -> str:
    icon = {BUY: "✓", CHECK: "?", WATCH: "◔", PASS: "✕"}[v.code]
    text = v.headline if v.code == BUY else LABEL[v.code]
    return f'<span class="vbadge {CLS[v.code]}{" big" if big else ""}"><span class="vi">{icon}</span>{e(text)}</span>'


def conf_meter(v: Verdict) -> str:
    bars = {"High": 3, "Medium": 2, "Low": 1}[v.confidence]
    tip = "How sure we are: " + SURE[v.confidence] + "\n" + "\n".join(v.confidence_why)
    return (f'<span class="conf-m" tabindex="0" data-tip="{e(tip)}">' + "".join(f'<i class="{"on" if i < bars else ""}"></i>' for i in range(3))
            + f'<small>{e(SURE[v.confidence])}</small></span>')


def _passcode(s: dict | None) -> str | None:
    m = re.search(r"@(\d+)", (s or {}).get("printing_id") or "")
    return m.group(1) if m else None


def _art(d: dict, v: Verdict, size: str = "small", cls: str = "card") -> str:
    from .ui_home import _listing_art, _lot_art, img
    if "LOT" in (d.get("kinds") or []):
        return _lot_art(_lot_size(d))
    return img(_listing_art(d), size, d.get("title") or "", cls=cls)


def _left(ends_at: str | None, now: datetime) -> str:
    if not ends_at:
        return ""
    try:
        sec = (datetime.fromisoformat(ends_at) - now).total_seconds()
    except ValueError:
        return ""
    if sec <= 0:
        return "ended"
    h = sec / 3600
    return f"ends in {int(sec // 60)} min" if h < 1 else f"ends in {h:.0f} h" if h < 48 else f"ends in {h / 24:.0f} days"


def listing_meta(st, key: str) -> dict:
    row = st.db.execute("SELECT kind, price, currency, url, data_json, source FROM listing WHERE key=?", (key,)).fetchone()
    if not row:
        return {}
    data = json.loads(row[4] or "{}")
    return {"kind": row[0], "price": row[1], "currency": row[2], "url": row[3], "bids": data.get("bids"), "shipping": data.get("shipping"),
            "source": row[5], "data": data}


def _type_label(meta: dict, d: dict) -> str:
    k = meta.get("kind")
    if k in ("bid", "auction"):
        b = meta.get("bids")
        return f"Auction{f' · {b} bids' if b else ''}"
    return "Buy it now" if k == "ask" else (k or "").title()


# -- the list ---------------------------------------------------------------------------------------------------------

def _likely(d: dict) -> dict | None:
    """The printing it most probably is (which can differ from the one a price came from)."""
    sc = d.get("scenarios") or []
    return max(sc, key=lambda s: s.get("weight") or 0) if sc else None


def row_html(key: str, d: dict, v: Verdict, cards, M, now: datetime, sparks: dict | None = None) -> str:
    """One listing as one line: picture, card, verdict, all-in vs max buy, confidence, time left. Shared by Home and Listings."""
    top = v.top
    lot = "LOT" in (d.get("kinds") or [])
    name, sub = d.get("title") or key, ""
    pc = _passcode(top)
    c = cards.card(pc) if (pc and cards is not None and not lot) else None
    if c and d.get("printing_id"):
        name = c.get("name_en") or name
    likely = _likely(d) or top
    if likely and likely.get("label") and not lot:
        sub = " · ".join(likely["label"].split(" · ")[:3]) + ("" if d.get("printing_id") else " (probably)")
    mb = top.get("max_buy") if top and top.get("basis") == "sales" else None
    sp = (sparks or {}).get(d.get("printing_id") or "") if not lot else ""
    return (f'<a class="lrow" href="/opp/{quote(key, safe="")}">{_art(d, v)}'
            f'<span class="lr-main"><span class="lr-title">{e(name)}</span><span class="lr-sub">{e(sub) or e(d.get("market") or "")}</span>'
            f'<span class="lr-reason">{e(v.reason)}</span>{sp or ""}</span>'
            f'<span class="lr-side">{badge(v)}<span class="lr-money"><small>Total</small> {M(d.get("all_in_home"))}'
            f'{"<small> · worth up to</small> " + M(mb) if mb is not None else ""}</span>'
            f'<span class="lr-meta">{conf_meter(v)} <span class="muted small">{e(_left(d.get("ends_at"), now))}</span></span></span></a>')


def decisions(st, M) -> list[tuple]:
    """Every listing with its verdict: (key, status, payload, verdict)."""
    out = []
    for key, status, pj in st.db.execute("SELECT key, status, payload_json FROM opportunity"):
        d = json.loads(pj)
        out.append((key, status, d, verdict(d, status, M)))
    return out


SITES = (("any", "All sites"), ("ebay", "eBay"), ("yahoo", "Yahoo! Auctions"), ("mercari", "Mercari"), ("other", "Other"))


def site_of(key: str) -> str:
    src = key.split(":", 1)[0].lower()
    return "ebay" if src.startswith("ebay") else "yahoo" if src.startswith("yahoo") else "mercari" if src.startswith("mercari") else "other"


def view_list(st, cards, now, M, *, v_filter: str, t_filter: str, home_cur: str, site: str = "any",
              pmin: float | None = None, pmax: float | None = None) -> str:
    items = decisions(st, M)
    counts = {c: sum(1 for i in items if i[3].code == c) for c in ORDER}
    is_lot = lambda d: "LOT" in (d.get("kinds") or [])
    shown = [i for i in items if (v_filter == "all" and i[3].code != PASS) or i[3].code == v_filter or v_filter == "every"]
    if t_filter == "singles":
        shown = [i for i in shown if not is_lot(i[2])]
    elif t_filter == "lots":
        shown = [i for i in shown if is_lot(i[2])]
    if site != "any":
        shown = [i for i in shown if site_of(i[0]) == site]
    k = float(getattr(M, "k", 1) or 1)                       # price filters are in the display currency
    if pmin is not None:
        shown = [i for i in shown if (i[2].get("all_in_home") or 0) * k >= pmin]
    if pmax is not None:
        shown = [i for i in shown if (i[2].get("all_in_home") or 0) * k <= pmax]
    shown.sort(key=lambda i: (ORDER[i[3].code], -(i[3].margin if i[3].margin is not None else -1e12), -(i[2].get("priority") or 0)))
    state = {"v": v_filter, "t": t_filter, "site": site, "min": "" if pmin is None else f"{pmin:g}", "max": "" if pmax is None else f"{pmax:g}"}

    def href(**kw):
        from urllib.parse import urlencode
        q = {**state, **kw}
        return "/opportunities?" + urlencode({a: b for a, b in q.items() if b not in ("", None)})

    def chip(param, val, text, n=None):
        on = state[param] == val
        return f'<a class="fchip{" on" if on else ""}" href="{e(href(**{param: val}))}">{e(text)}' + (f' <b>{n}</b>' if n is not None else "") + '</a>'
    bar = ('<div class="filterbar">'
           '<div class="fchips">' + chip("v", "all", "Worth a look", sum(counts[c] for c in (BUY, CHECK, WATCH)))
           + chip("v", BUY, "Buy", counts[BUY]) + chip("v", CHECK, "Check first", counts[CHECK]) + chip("v", WATCH, "Watch", counts[WATCH])
           + chip("v", PASS, "Skip", counts[PASS]) + '</div><div class="fchips">'
           + chip("t", "any", "Single cards and lots") + chip("t", "singles", "Single cards") + chip("t", "lots", "Lots")
           + '<span class="sep"></span>' + "".join(chip("site", v, label) for v, label in SITES) + '</div>'
           f'<form class="prange" method="get" action="/opportunities">'
           + "".join(f'<input type="hidden" name="{a}" value="{e(b)}">' for a, b in state.items() if a in ("v", "t", "site"))
           + f'<label>Total from <input name="min" value="{e(state["min"])}" inputmode="decimal" size="6"></label>'
           f'<label>to <input name="max" value="{e(state["max"])}" inputmode="decimal" size="6"></label>'
           f'<span class="muted small">{e(getattr(M, "cur", ""))}</span><button>Apply</button>'
           + (f'<a class="small" href="{e(href(min="", max=""))}">clear</a>' if pmin is not None or pmax is not None else "")
           + '</form></div>')
    from .history import sparks as _sparks
    sp = _sparks(st, now, home_cur)
    rows = [row_html(key, d, v, cards, M, now, sp) for key, status, d, v in shown[:150]]
    hidden = "" if v_filter in (PASS, "every") else f'<p class="muted small">{counts[PASS]} skipped listings are hidden. <a href="{e(href(v=PASS))}">Show them</a>.</p>'
    more = f'<p class="muted small">Showing the first 150 of {len(shown)}. Narrow the filters to see the rest.</p>' if len(shown) > 150 else ""
    return ('<h1>Listings</h1><p class="sub">Every listing with our take. We only say Buy when real sales back the price.</p>'
            + bar + hidden
            + (f'<div class="lrows">{"".join(rows)}</div>' if rows else '<p class="empty">Nothing matches these filters. Try “Worth a look” or clear the price range.</p>')
            + more)


# -- one listing ------------------------------------------------------------------------------------------------------

def view_one(st, cards, key: str, now: datetime, token: str, M, profile: dict | None) -> str:
    row = st.db.execute("SELECT stage, status, payload_json, snapshot_id FROM opportunity WHERE key=?", (key,)).fetchone()
    if not row:
        raise KeyError(key)
    stage, status, pj, sid = row
    d = json.loads(pj)
    v = verdict(d, status, M)
    meta = listing_meta(st, key)
    top = v.top
    pc = _passcode(top)
    c = cards.card(pc) if (pc and cards is not None) else None
    lot = "LOT" in (d.get("kinds") or [])
    settled = bool(d.get("printing_id"))
    card_line = ("Lot: " + (f"{_lot_size(d)} cards" if _lot_size(d) else "several cards")) if lot else \
        (" · ".join((_likely(d) or top or {}).get("label", "").split(" · ")[:3]) + ("" if settled else "  (probably; version not certain)")) \
        if (_likely(d) or top) else "Card not identified yet"

    # -- key facts
    def fact(label, value, note=""):
        return f'<div class="fact"><small>{e(label)}</small><b>{value}</b>{f"<em>{note}</em>" if note else ""}</div>'
    ship = meta.get("shipping")
    landed_note = f"price {M(d.get('ask_home'))}" + (" + shipping" if ship else "") + " + fees and import"
    if top and top.get("basis") == "sales":
        mv, mv_note = M(top.get("resale_mid")), f'<span class="ev good">{top.get("n_sales", 0)} recent sales</span> low {M(top.get("resale_low"))}'
    elif top and top.get("basis") == "reference":
        mv, mv_note = M(top.get("resale_mid")), '<span class="ev warn">price guide</span> not a real sale'
    else:
        mv, mv_note = "—", '<span class="ev">no sales seen yet</span>'
    mb = M(top.get("max_buy")) if top and top.get("basis") == "sales" and top.get("max_buy") is not None else "—"
    mb_note = "from real sales and your costs" if mb != "—" else "needs 3+ real sales"
    ep = d.get("expected_profit")
    ep_note = "after every cost" if ep is not None else ("no real sales yet" if not _sales_scenarios(d) else "not sure enough which version it is")
    facts = ('<div class="facts">'
             + fact("Total to you", M(d.get("all_in_home")), landed_note)
             + fact("Usually sells for", mv, mv_note)
             + fact("Worth paying up to", mb, mb_note)
             + fact("Profit if you buy", M(ep, True) if ep is not None else "—", ep_note)
             + fact("Listing", e(_type_label(meta, d)), e(_left(d.get("ends_at"), now) or {"INTL": "ships internationally", "JP": "in Japan",
                                                                                            "US": "in the US", "TW": "in Taiwan"}.get(d.get("market") or "", "")))
             + '</div>')

    url = d.get("url") or meta.get("url")
    actions = []
    if url and url.startswith(("http://", "https://")):
        actions.append(f'<a class="btn" href="{e(url)}" target="_blank" rel="noreferrer noopener">Open the listing ↗</a>')
    if v.code == CHECK or not settled:
        actions.append('<a class="btn ghost" href="#confirm">Tell us which version it is</a>')
    if c:
        actions.append(f'<a class="btn ghost" href="/card/{e(pc)}">See this card\'s prices</a>')
    if sid:
        actions.append(f'<a class="btn ghost" href="/deal/{e(sid)}">Full math</a>')

    hero = (f'<section class="lhero vcard-{CLS[v.code]}"><div class="lh-art">{_art(d, v, "", "card big")}</div><div class="lh-body">'
            f'{badge(v, big=True)}<p class="lh-reason">{e(v.reason)}</p>'
            f'<h1 class="lh-title">{e((c or {}).get("name_en") if (c and settled and not lot) else d.get("title") or key)}</h1>'
            f'<p class="lh-card">{e(card_line)}</p>'
            + (f'<p class="lh-seller muted small">Listed as: {e(d.get("title"))}</p>' if (c and settled and not lot) else "")
            + f'<p class="lh-conf">How sure we are {conf_meter(v)}</p>'
            + f'<div class="lh-actions">{"".join(actions)}</div></div></section>')

    # -- details, closed by default
    sc = d.get("scenarios") or []
    priced_rows = "".join(
        f'<tr><td>{e(s.get("label"))}</td><td class="n">{(s.get("weight") or 0) * 100:.0f}%</td>'
        f'<td>{e({"sales": "real sales", "reference": "price guide", "none": "no price"}.get(s.get("basis"), s.get("basis")))}</td>'
        f'<td class="n">{s.get("n_sales") or 0}</td><td class="n">{M(s.get("resale_low"))}</td><td class="n">{M(s.get("resale_mid"))}</td>'
        f'<td class="n">{M(s.get("max_buy"))}</td><td class="n">{"—" if s.get("p_loss") is None else f"{s['p_loss'] * 100:.0f}%"}</td>'
        f'<td class="n">{"—" if s.get("months_to_sell") is None else f"{s['months_to_sell']:.1f}"}</td></tr>'
        for s in sc if s.get("basis") != "none")
    unpriced = [s for s in sc if s.get("basis") == "none"]
    pricing = ('<details class="dsec"><summary>How we got this price</summary>'
               + (('<div class="wrap"><table><tr><th>Version</th><th class="n">Chance it\'s this one</th><th>Price from</th><th class="n">Sales</th>'
                   '<th class="n">Low</th><th class="n">Typical</th><th class="n">Worth up to</th><th class="n">Chance of a loss</th>'
                   '<th class="n">Months to sell</th></tr>' + priced_rows + '</table></div>') if priced_rows else '<p class="muted">None of the versions it could be has a price yet.</p>')
               + (f'<p class="muted small">{len(unpriced)} other version{"s" if len(unpriced) != 1 else ""} it could be '
                  f'{"have" if len(unpriced) != 1 else "has"} no price yet.</p>' if unpriced else "")
               + '<p class="why small">Low and typical come from recent real sales of that exact version. "Worth up to" is the most you can pay '
               'and still make your target profit after all costs. Price guides never set it.</p></details>')

    plain = {"CODE_NAME_MISMATCH": "Name and set code don't match", "RARITY_NOT_ON_CODE": "That rarity doesn't exist for this set code",
             "PHOTO_CONTRADICTS_SELLER": "The photo doesn't match the title", "RARITY_UNSTATED": "No rarity given",
             "SELLER_UNSURE": "The seller isn't sure", "PHOTO_ADDS_CODE": "The photo shows the set code"}
    findings = "".join(f'<li><b>{e(plain.get(f["kind"], f["kind"].replace("_", " ").capitalize()))}</b>: {e(f["text"])}</li>'
                       for f in d.get("findings") or [])
    cand_rows = "".join(f'<li>{e(s.get("label"))} <span class="muted">{(s.get("weight") or 0) * 100:.0f}%</span></li>'
                        for s in sorted(sc, key=lambda s: -(s.get("weight") or 0))[:12])
    identity = ('<details class="dsec" id="identity"><summary>What we think it is</summary>'
                + (f'<p><b>{"We know which version it is." if settled else "Not certain yet."}</b> '
                   + ("" if settled else f"From the title it could be {_n_candidates(d)} versions, most likely first:")
                   + '</p>')
                + (f'<ol class="cands">{cand_rows}</ol>' if cand_rows and not settled else "")
                + (f'<ul class="flist">{findings}</ul>' if findings else '<p class="muted">Nothing odd in the listing.</p>')
                + '</details>')

    costs = ""
    if profile:
        try:
            from .opportunity import costs_for
            acq, ex = costs_for(profile, d.get("market") or "INTL")
            a, b = acq.linear()
            costs = ('<details class="dsec"><summary>Your costs</summary><div class="wrap"><table class="math">'
                     f'<tr><td>Price</td><td class="n">{M(d.get("ask_home"))}</td></tr>'
                     f'<tr><td>Buying costs that grow with the price (proxy service, currency exchange, import tax)</td><td class="n">{float(a - 1) * 100:.1f}%</td></tr>'
                     f'<tr><td>Fixed buying costs (shipping to you, insurance, fees)</td><td class="n">{M(float(b))}</td></tr>'
                     f'<tr class="total"><td>Total to you</td><td class="n">{M(d.get("all_in_home"))}</td></tr>'
                     f'<tr><td>Selling costs when you resell (fees, payment, returns)</td><td class="n">'
                     f'{float(ex.platform_fee_pct + ex.payment_fee_pct + ex.returns_reserve_pct + ex.payout_fx_spread_pct) * 100:.1f}%</td></tr>'
                     f'<tr><td>Shipping to your buyer</td><td class="n">{M(float(ex.outbound_shipping))}</td></tr></table></div>'
                     + ('<p class="note small">These are starter costs. <a href="/costs">Set yours</a>.</p>' if profile.get("_assumed") else "")
                     + '</details>')
        except Exception:
            costs = ""

    state = {"OPEN": "Open", "ESCALATED": "Worth a closer look", "SHELVED": "Set aside", "REJECTED": "Skipped"}.get(status, status)
    raw = ('<details class="dsec"><summary>Technical details</summary><dl class="ids">'
           f'<dt>Listing ID</dt><dd><code>{e(key)}</code></dd><dt>Found on</dt><dd>{e(meta.get("source"))}</dd>'
           f'<dt>Original price</dt><dd>{e(meta.get("price"))} {e(meta.get("currency"))}</dd>'
           f'<dt>Status</dt><dd>{e(state)}</dd>'
           f'<dt>What a check could be worth</dt><dd>{M(d.get("evpi"))}</dd>'
           f'<dt>Versions with a price</dt><dd>{(d.get("priced_share") or 0) * 100:.0f}% of the chance</dd>'
           + "".join(f'<dt>Next step</dt><dd>{e(a["what"])} ({a["minutes"]:g} min)</dd>' for a in d.get("research") or [])
           + '</dl></details>')

    form = (f'<section id="confirm" class="confirm{" open" if v.code == CHECK else ""}"><h2>Which version is it?</h2>'
            '<p class="why">Look at the card or a clear photo and fill in only what you can see. Then we can price it from real sales.</p>'
            f'<form method="post" action="/opp/{quote(key, safe="")}/confirm" class="scanform"><input type="hidden" name="t" value="{e(token)}">'
            '<div class="row"><label>Set code printed on the card<input name="printed_set_code" size="12" placeholder="LOB-EN001"></label>'
            '<label>Rarity (from the shine)<input name="verified_rarity" size="16" placeholder="Ultra Rare"></label>'
            '<label>Edition<select name="verified_edition"><option value=""></option><option value="1st">1st Edition</option><option>Unlimited</option>'
            '<option>Limited</option></select></label><label>Language<select name="card_language"><option value=""></option>'
            '<option value="ja">Japanese</option><option value="en">English</option></select></label>'
            '<label>Real card?<select name="authenticity_ok"><option value=""></option><option value="yes">Looks real</option>'
            '<option value="no">Fake or not sure</option></select></label></div>'
            '<div class="row"><button>Save</button><span class="muted small">We\'ll update the price on the next check.</span></div></form></section>')
    return hero + facts + form + pricing + identity + costs + raw


LISTING_CSS = """
.vbadge{display:inline-flex;align-items:center;gap:7px;font-weight:800;font-size:13px;padding:3px 12px 3px 4px;border-radius:999px;
border:1.5px solid var(--axis);white-space:nowrap;letter-spacing:.01em}.vbadge .vi{width:20px;height:20px;border-radius:50%;display:inline-flex;
align-items:center;justify-content:center;color:#fff;font-size:12px;background:var(--muted)}
.vbadge.good{border-color:var(--good);color:var(--good-text)}.vbadge.good .vi{background:var(--good)}
.vbadge.warn{border-color:var(--warn);color:#8a5a00}.vbadge.warn .vi{background:var(--warn);color:#0b0b0b}
.vbadge.info{border-color:var(--accent);color:var(--accent)}.vbadge.info .vi{background:var(--accent)}
.vbadge.crit{border-color:var(--axis);color:var(--fg2)}.vbadge.crit .vi{background:var(--crit)}
.vbadge.big{font-size:19px;padding:5px 18px 5px 6px}.vbadge.big .vi{width:28px;height:28px;font-size:15px}
.conf-m{display:inline-flex;align-items:center;gap:2px;vertical-align:middle;cursor:help}.conf-m i{width:6px;height:12px;border-radius:2px;
background:var(--line);display:inline-block}.conf-m i.on{background:var(--fg2)}.conf-m small{margin-left:5px;font-size:12px;color:var(--fg2)}
.fchips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px;align-items:center}
.filterbar{position:sticky;top:58px;z-index:20;background:var(--bg);padding:10px 0 4px;margin:0 0 6px;border-bottom:1px solid var(--line)}
.prange{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin:0 0 6px}.prange label{flex-direction:row;align-items:center;gap:6px}
@media(max-width:860px){.filterbar{top:0;position:static}}.fchips .sep{width:10px}
.fchip{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--fg2);text-decoration:none}
.fchip.on{background:var(--fg);color:var(--surface);border-color:var(--fg)}.fchip b{font-weight:700}
.lrows{display:grid;gap:8px}.lrow{display:grid;grid-template-columns:56px minmax(0,1fr) auto;gap:14px;align-items:center;padding:10px 14px;
background:var(--surface);border:1px solid var(--line);border-radius:12px;color:var(--fg);text-decoration:none}
.lrow:hover{box-shadow:0 6px 18px rgba(0,0,0,.07)}.lrow .card{width:56px;height:82px}
.lr-main{display:flex;flex-direction:column;gap:2px;min-width:0}.lr-title{font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lr-sub{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lr-reason{font-size:13px;color:var(--fg2)}
.lr-side{display:flex;flex-direction:column;align-items:flex-end;gap:4px;text-align:right}.lr-money{font-variant-numeric:tabular-nums;font-weight:650}
.lr-money small{font-weight:500;color:var(--muted);font-size:11px}
@media(max-width:700px){.lrow{grid-template-columns:48px minmax(0,1fr)}.lrow .card{width:48px;height:70px}.lr-side{grid-column:1/-1;flex-direction:row;
flex-wrap:wrap;justify-content:space-between;align-items:center}}
.lhero{display:grid;grid-template-columns:minmax(160px,240px) minmax(0,1fr);gap:24px;align-items:start;border-left-width:5px}
.lhero.vcard-good{border-left-color:var(--good)}.lhero.vcard-warn{border-left-color:var(--warn)}.lhero.vcard-info{border-left-color:var(--accent)}
.lhero.vcard-crit{border-left-color:var(--crit)}@media(max-width:700px){.lhero{grid-template-columns:1fr}.lh-art{max-width:180px}}
.lh-reason{font-size:17px;margin:10px 0 6px;max-width:62ch}.lh-title{font-size:24px;margin:8px 0 2px;display:block}
.lh-card{color:var(--fg2);margin:0 0 4px}.lh-conf{margin:8px 0}.lh-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.btn{display:inline-block;padding:8px 14px;border-radius:9px;background:var(--fg);color:var(--surface);text-decoration:none;font-weight:600;font-size:13px}
.btn.ghost{background:var(--surface);color:var(--fg);border:1px solid var(--axis)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(170px,100%),1fr));gap:10px;margin:0 0 14px}
.fact{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 14px}.fact small{display:block;color:var(--muted);font-size:12px}
.fact b{display:block;font-size:21px;font-variant-numeric:tabular-nums;margin:2px 0}.fact em{font-style:normal;font-size:12px;color:var(--fg2)}
.ev{display:inline-block;font-size:11px;font-weight:700;padding:0 7px;border-radius:999px;border:1px solid var(--axis);color:var(--fg2)}
.ev.good{color:var(--good-text);border-color:var(--good)}.ev.warn{color:#8a5a00;border-color:var(--warn)}
details.dsec{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:0 0 10px}
details.dsec>summary{font-weight:650;color:var(--fg);font-size:15px}details.dsec[open]>summary{margin-bottom:10px}
.confirm{display:none}.confirm.open,.confirm:target{display:block}.cands{margin:4px 0 8px;padding-left:22px}.flist{margin:6px 0;padding-left:18px}
"""
