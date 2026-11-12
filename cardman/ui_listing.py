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
LABEL = {BUY: "Buy", CHECK: "Needs your check", WATCH: "Watch", PASS: "Pass"}
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
    kinds = set(d.get("kinds") or [])
    sales = _sales_scenarios(d)
    share = float(d.get("priced_share") or 0)
    ncand = _n_candidates(d)
    resolved = bool(d.get("printing_id"))
    all_in = d.get("all_in_home")
    top = max(sales, key=lambda s: s.get("weight") or 0) if sales else None
    ref = next((s for s in sorted(d.get("scenarios") or [], key=lambda s: -(s.get("weight") or 0)) if s.get("basis") == "reference"), None)

    why = []
    why.append("identity settled" if resolved else f"identity open: {ncand} possible printing{'s' if ncand != 1 else ''}" if ncand else "no card matched")
    n = (top or {}).get("n_sales") or 0
    why.append(f"{n} recent sale{'s' if n != 1 else ''} behind the price" if top else "no real sales yet")
    why.append(f"{share:.0%} of the possibilities priced")
    level = "High" if resolved and n >= 6 and share >= 0.8 else "Medium" if share >= 0.5 and n >= 3 else "Low"

    margin = (top.get("max_buy") - all_in) if top and top.get("max_buy") is not None and all_in is not None else None

    def V(code, headline, reason):
        return Verdict(code, headline, reason, level, why, top or ref, margin)

    if status == "REJECTED":
        block = next((f["text"] for f in d.get("findings") or [] if f.get("severity") == "BLOCK"), None)
        return V(PASS, "Pass", block or (d.get("reasons") or ["Out of scope"])[0])
    if "LOT" in kinds and "LOT_UNDERPRICED" not in kinds:
        size = _lot_size(d)
        what = f"Lot of {size}" if size else "Lot"
        if status in ("OPEN", "ESCALATED"):
            cue = "seller doesn't know what they have" if "SELLER_UNSURE" in kinds else "collection or early-era cards" if "COLLECTION" in kinds else "worth a look"
            return V(WATCH, "Watch", f"{what}: can't value it per card yet; {cue}. A card list or photos would price it.")
        return V(PASS, "Pass", f"{what} with nothing that can be priced and no sign it's underpriced.")
    if "LOT_UNDERPRICED" in kinds:
        return V(CHECK, "Needs your check", f"The cards it names resell for more than the lot costs ({M(d.get('expected_profit'), True)} after fees). "
                 "Check the card list and condition.")
    if status == "SHELVED" and not (kinds & IDENTITY_KINDS):
        return V(PASS, "Pass", (d.get("reasons") or ["Nothing here says it is worth more than the ask."])[-1])
    if top and top.get("max_buy") is not None and all_in is not None:
        if all_in <= top["max_buy"] and resolved and "UNDERPRICED" in kinds:
            return V(BUY, f"Buy up to {M(top['max_buy'])}",
                     f"All-in {M(all_in)} vs max buy {M(top['max_buy'])}, from {n} recent sale{'s' if n != 1 else ''}.")
        if all_in <= top["max_buy"]:
            return V(CHECK, "Needs your check", f"Under max buy if it is {top.get('label')}: could be {ncand} printings, confirm which.")
        if not (kinds & IDENTITY_KINDS) and (d.get("best_profit") is None or d.get("best_profit") <= 0):
            return V(PASS, "Pass", f"All-in {M(all_in)} is over the max buy {M(top['max_buy'])} for every priced printing.")
    if kinds & IDENTITY_KINDS:
        f = next((f["text"] for f in d.get("findings") or [] if f.get("kind") in ("CODE_NAME_MISMATCH", "RARITY_NOT_ON_CODE",
                                                                               "PHOTO_CONTRADICTS_SELLER", "RARITY_UNSTATED")), None)
        return V(CHECK, "Needs your check", f or f"Could be {ncand} printings and the price depends on which: confirm from the photos.")
    if "REFERENCE_GAP" in kinds or ref:
        return V(WATCH, "Watch", "No sales yet: the price is a guide estimate only.")
    if "SELLER_UNSURE" in kinds:
        return V(WATCH, "Watch", "Seller says they don't know the card: mispricing is likely, but nothing is priced yet.")
    if status in ("OPEN", "ESCALATED"):
        return V(WATCH, "Watch", "Something here may be worth following, but no sales price it yet.")
    return V(PASS, "Pass", (d.get("reasons") or ["Nothing here says it is worth more than the ask."])[-1])


def badge(v: Verdict, big: bool = False) -> str:
    icon = {BUY: "✓", CHECK: "?", WATCH: "◔", PASS: "✕"}[v.code]
    text = v.headline if v.code == BUY else LABEL[v.code]
    return f'<span class="vbadge {CLS[v.code]}{" big" if big else ""}"><span class="vi">{icon}</span>{e(text)}</span>'


def conf_meter(v: Verdict) -> str:
    bars = {"High": 3, "Medium": 2, "Low": 1}[v.confidence]
    tip = "Confidence: " + v.confidence + "\n" + "\n".join(v.confidence_why)
    return (f'<span class="conf-m" tabindex="0" data-tip="{e(tip)}">' + "".join(f'<i class="{"on" if i < bars else ""}"></i>' for i in range(3))
            + f'<small>{e(v.confidence)}</small></span>')


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

def view_list(st, cards, now, M, *, v_filter: str, t_filter: str, home_cur: str) -> str:
    rows = st.db.execute("SELECT o.key, o.status, o.payload_json FROM opportunity o").fetchall()
    items = []
    for key, status, pj in rows:
        d = json.loads(pj)
        items.append((key, status, d, verdict(d, status, M)))
    counts = {c: sum(1 for i in items if i[3].code == c) for c in ORDER}
    is_lot = lambda d: "LOT" in (d.get("kinds") or [])
    shown = [i for i in items if (v_filter == "all" and i[3].code != PASS) or i[3].code == v_filter or (v_filter == "every")]
    if t_filter == "singles":
        shown = [i for i in shown if not is_lot(i[2])]
    elif t_filter == "lots":
        shown = [i for i in shown if is_lot(i[2])]
    shown.sort(key=lambda i: (ORDER[i[3].code], -(i[3].margin if i[3].margin is not None else -1e12), -(i[2].get("priority") or 0)))

    def chip(param, val, text, n=None):
        on = (v_filter if param == "v" else t_filter) == val
        q = {"v": v_filter, "t": t_filter, param: val}
        return (f'<a class="fchip{" on" if on else ""}" href="/opportunities?v={e(q["v"])}&t={e(q["t"])}">{e(text)}'
                + (f' <b>{n}</b>' if n is not None else "") + '</a>')
    chips = ('<div class="fchips">' + chip("v", "all", "All but pass", sum(counts[c] for c in (BUY, CHECK, WATCH)))
             + chip("v", BUY, "Buy", counts[BUY]) + chip("v", CHECK, "Needs check", counts[CHECK]) + chip("v", WATCH, "Watch", counts[WATCH])
             + chip("v", PASS, "Pass", counts[PASS]) + '<span class="sep"></span>' + chip("t", "any", "Singles and lots")
             + chip("t", "singles", "Singles") + chip("t", "lots", "Lots") + '</div>')
    cards_html = []
    for key, status, d, v in shown[:150]:
        top = v.top
        name, sub = d.get("title") or key, ""
        pc = _passcode(top)
        c = cards.card(pc) if (pc and cards is not None and not is_lot(d)) else None
        if c and d.get("printing_id"):
            name = c.get("name_en") or name
        if top and top.get("label"):
            sub = top["label"].split(" · ")
            sub = " · ".join(sub[:3])
        mb = top.get("max_buy") if top and top.get("basis") == "sales" else None
        cards_html.append(
            f'<a class="lrow" href="/opp/{quote(key, safe="")}">{_art(d, v)}'
            f'<span class="lr-main"><span class="lr-title">{e(name)}</span><span class="lr-sub">{e(sub) or e(d.get("market") or "")}</span>'
            f'<span class="lr-reason">{e(v.reason)}</span></span>'
            f'<span class="lr-side">{badge(v)}<span class="lr-money"><small>All-in</small> {M(d.get("all_in_home"))}'
            f'{"<small> · max buy</small> " + M(mb) if mb is not None else ""}</span>'
            f'<span class="lr-meta">{conf_meter(v)} <span class="muted small">{e(_left(d.get("ends_at"), now))}</span></span></span></a>')
    hidden = "" if v_filter in (PASS, "every") else f'<p class="muted small">{counts[PASS]} passed listings are hidden. <a href="/opportunities?v=PASS&t={e(t_filter)}">Show them</a>.</p>'
    return ('<h1>Listings</h1><p class="sub">Each listing as one decision: Buy, Needs your check, Watch or Pass. Buy only ever appears when real '
            'sales back the price.</p>' + chips + hidden
            + (f'<div class="lrows">{"".join(cards_html)}</div>' if cards_html else '<p class="empty">Nothing here with these filters.</p>'))


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
        (" · ".join((top or {}).get("label", "").split(" · ")[:3]) + ("" if settled else "  (most likely; not confirmed)")) if top else "Card not identified yet"

    # -- key facts
    def fact(label, value, note=""):
        return f'<div class="fact"><small>{e(label)}</small><b>{value}</b>{f"<em>{note}</em>" if note else ""}</div>'
    ship = meta.get("shipping")
    landed_note = f"ask {M(d.get('ask_home'))}" + (" + shipping" if ship else "") + " + fees and import"
    if top and top.get("basis") == "sales":
        mv, mv_note = M(top.get("resale_mid")), f'<span class="ev good">{top.get("n_sales", 0)} sales</span> low {M(top.get("resale_low"))}'
    elif top and top.get("basis") == "reference":
        mv, mv_note = M(top.get("resale_mid")), '<span class="ev warn">guide est.</span> not a sale'
    else:
        mv, mv_note = "—", '<span class="ev">no sales</span>'
    mb = M(top.get("max_buy")) if top and top.get("basis") == "sales" and top.get("max_buy") is not None else "—"
    mb_note = "from sold prices and your costs" if mb != "—" else "needs 3+ real sales"
    ep = d.get("expected_profit")
    ep_note = "after every cost" if ep is not None else ("no real sales yet" if not _sales_scenarios(d) else "under half of the possibilities priced")
    facts = ('<div class="facts">'
             + fact("All-in landed", M(d.get("all_in_home")), landed_note)
             + fact("Market value", mv, mv_note)
             + fact("Max buy", mb, mb_note)
             + fact("Expected profit", M(ep, True) if ep is not None else "—", ep_note)
             + fact("Listing", e(_type_label(meta, d)), e(_left(d.get("ends_at"), now) or d.get("market") or ""))
             + '</div>')

    url = d.get("url") or meta.get("url")
    actions = []
    if url and url.startswith(("http://", "https://")):
        actions.append(f'<a class="btn" href="{e(url)}" target="_blank" rel="noreferrer noopener">Open the listing ↗</a>')
    if v.code == CHECK or not settled:
        actions.append('<a class="btn ghost" href="#confirm">Confirm the card</a>')
    if c:
        actions.append(f'<a class="btn ghost" href="/card/{e(pc)}">Card page &amp; sold prices</a>')
    if sid:
        actions.append(f'<a class="btn ghost" href="/deal/{e(sid)}">Full valuation</a>')

    hero = (f'<section class="lhero vcard-{CLS[v.code]}"><div class="lh-art">{_art(d, v, "", "card big")}</div><div class="lh-body">'
            f'{badge(v, big=True)}<p class="lh-reason">{e(v.reason)}</p>'
            f'<h1 class="lh-title">{e((c or {}).get("name_en") if (c and settled and not lot) else d.get("title") or key)}</h1>'
            f'<p class="lh-card">{e(card_line)}</p>'
            + (f'<p class="lh-seller muted small">Listed as: {e(d.get("title"))}</p>' if (c and settled and not lot) else "")
            + f'<p class="lh-conf">Confidence {conf_meter(v)}</p>'
            + f'<div class="lh-actions">{"".join(actions)}</div></div></section>')

    # -- details, closed by default
    sc = d.get("scenarios") or []
    priced_rows = "".join(
        f'<tr><td>{e(s.get("label"))}</td><td class="n">{(s.get("weight") or 0) * 100:.0f}%</td>'
        f'<td>{e({"sales": "sold prices", "reference": "guide estimate", "none": "no price"}.get(s.get("basis"), s.get("basis")))}</td>'
        f'<td class="n">{s.get("n_sales") or 0}</td><td class="n">{M(s.get("resale_low"))}</td><td class="n">{M(s.get("resale_mid"))}</td>'
        f'<td class="n">{M(s.get("max_buy"))}</td><td class="n">{"—" if s.get("p_loss") is None else f"{s["p_loss"] * 100:.0f}%"}</td>'
        f'<td class="n">{"—" if s.get("months_to_sell") is None else f"{s["months_to_sell"]:.1f}"}</td></tr>'
        for s in sc if s.get("basis") != "none")
    unpriced = [s for s in sc if s.get("basis") == "none"]
    pricing = ('<details class="dsec"><summary>How we priced it</summary>'
               + (('<div class="wrap"><table><tr><th>Printing</th><th class="n">Chance</th><th>Price from</th><th class="n">Sales</th>'
                   '<th class="n">Low</th><th class="n">Mid</th><th class="n">Max buy</th><th class="n">Chance of loss</th><th class="n">Months to sell</th></tr>'
                   + priced_rows + '</table></div>') if priced_rows else '<p class="muted">No printing it could be has a price yet.</p>')
               + (f'<p class="muted small">{len(unpriced)} other possible printing(s) have no price yet.</p>' if unpriced else "")
               + '<p class="why small">Low and mid are the low end and middle of recent sold prices for that exact printing; max buy is the '
               'most you can pay and still reach your target return after every cost, at your confidence level. Guide estimates never set '
               'a max buy.</p></details>')

    findings = "".join(f'<li><b>{e(f["kind"].replace("_", " ").title())}</b>: {e(f["text"])}</li>' for f in d.get("findings") or [])
    cand_rows = "".join(f'<li>{e(s.get("label"))} <span class="muted">{(s.get("weight") or 0) * 100:.0f}%</span></li>'
                        for s in sorted(sc, key=lambda s: -(s.get("weight") or 0))[:12])
    identity = ('<details class="dsec" id="identity"><summary>Identity match</summary>'
                + (f'<p><b>{"Confirmed" if settled else "Not confirmed yet"}.</b> '
                   + ("This is the exact printing." if settled else f"From the title alone it could be {_n_candidates(d)} printings; the most likely:")
                   + '</p>')
                + (f'<ol class="cands">{cand_rows}</ol>' if cand_rows and not settled else "")
                + (f'<ul class="flist">{findings}</ul>' if findings else '<p class="muted">Nothing unusual in the listing text.</p>')
                + '</details>')

    costs = ""
    if profile:
        try:
            from decimal import Decimal
            from .opportunity import costs_for
            acq, ex = costs_for(profile, d.get("market") or "INTL")
            a, b = acq.linear()
            costs = ('<details class="dsec"><summary>Costs breakdown</summary><div class="wrap"><table class="math">'
                     f'<tr><td>Ask</td><td class="n">{M(d.get("ask_home"))}</td></tr>'
                     f'<tr><td>Per-price costs (proxy, FX spread, import duty) × ask</td><td class="n">{float(a - 1) * 100:.1f}%</td></tr>'
                     f'<tr><td>Fixed costs (shipping in, insurance, fixed fees)</td><td class="n">{M(float(b))}</td></tr>'
                     f'<tr class="total"><td>All-in landed</td><td class="n">{M(d.get("all_in_home"))}</td></tr>'
                     f'<tr><td>Selling fee + payment + returns reserve (when you resell)</td><td class="n">'
                     f'{float(ex.platform_fee_pct + ex.payment_fee_pct + ex.returns_reserve_pct + ex.payout_fx_spread_pct) * 100:.1f}%</td></tr>'
                     f'<tr><td>Shipping to your buyer</td><td class="n">{M(float(ex.outbound_shipping))}</td></tr></table></div>'
                     + ('<p class="note small">These are the assumed starter costs. <a href="/costs">Set yours</a>.</p>' if profile.get("_assumed") else "")
                     + '</details>')
        except Exception:
            costs = ""

    raw = ('<details class="dsec"><summary>Raw listing data</summary><dl class="ids">'
           f'<dt>Listing key</dt><dd><code>{e(key)}</code></dd><dt>Source</dt><dd>{e(meta.get("source"))}</dd>'
           f'<dt>Original ask</dt><dd>{e(meta.get("price"))} {e(meta.get("currency"))}</dd>'
           f'<dt>Engine status</dt><dd>{e(status)} · stage {e(stage)}</dd>'
           f'<dt>Value of a check</dt><dd>{M(d.get("evpi"))}</dd>'
           f'<dt>Priced share</dt><dd>{(d.get("priced_share") or 0) * 100:.0f}%</dd>'
           + "".join(f'<dt>Note</dt><dd>{e(r)}</dd>' for r in d.get("reasons") or [])
           + "".join(f'<dt>Next check</dt><dd>{e(a["what"])} ({a["minutes"]:g} min)</dd>' for a in d.get("research") or [])
           + '</dl></details>')

    form = (f'<section id="confirm" class="confirm{" open" if v.code == CHECK else ""}"><h2>Confirm from the card</h2>'
            '<p class="why">Only what you can see on the card or a clear photo. This settles the printing and lets real sold prices value it.</p>'
            f'<form method="post" action="/opp/{quote(key, safe="")}/confirm" class="scanform"><input type="hidden" name="t" value="{e(token)}">'
            '<div class="row"><label>Printed set code<input name="printed_set_code" size="12" placeholder="LOB-EN001"></label>'
            '<label>Rarity (from the foil)<input name="verified_rarity" size="16" placeholder="Ultra Rare"></label>'
            '<label>Edition stamp<select name="verified_edition"><option value=""></option><option>1st</option><option>Unlimited</option>'
            '<option>Limited</option></select></label><label>Card language<select name="card_language"><option value=""></option>'
            '<option value="ja">Japanese</option><option value="en">English</option></select></label>'
            '<label>Authentic?<select name="authenticity_ok"><option value=""></option><option value="yes">Looks genuine</option>'
            '<option value="no">Fake or doubtful</option></select></label></div>'
            '<div class="row"><button>Confirm</button><span class="muted small">Re-analyzed on the next check.</span></div></form></section>')
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
.fchips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px;align-items:center}.fchips .sep{width:10px}
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
