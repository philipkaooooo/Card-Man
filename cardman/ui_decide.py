"""Decisions, made visible: the decision rail (Buy / 2x Check / Watch / Skip with live counts) and the listing card.

Every part answers one question for the person deciding:
  rail          How many of each decision, and show me only those.            (counts, one click to filter)
  big number    How much under or over its value is it?                       ("$38 under value")
  value bar     Price against value, and the size of the saving.              (orange fill = the saving)
  why line      Why this decision, in one plain sentence.                     (the verdict's own reason)
  check chip    What exactly needs a human look.                              ("Edition not stated")
  target ring   What price would make it a Buy.                               ("Buy at $30 or less")
  basis line    What the value comes from.                                    ("Based on 14 recent sales")
Everything else is folded under "Details".

Motion: one per decision, transform/opacity only, 150–350 ms, ease-out, played once when the card first comes into
view, never looping, and off under prefers-reduced-motion (the final state is the default state, so nothing is lost).
  Buy       the value bar fills from the price to the value; a single soft glow if it is new since your last visit
  2x Check  one light sweep across the photo that lands on the thing to check
  Watch     the ring's ticks fill to show how close the ask is to a Buy price
  Skip      the card settles and dims, reason still shown
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import quote

from .ui_listing import BUY, CHECK, LABEL, PASS, WATCH, Verdict, _art, _likely, _passcode, item_id_of, listing_icons, lphoto

LANE = {BUY: "buy", CHECK: "check", WATCH: "watch", PASS: "skip"}
LANE_WHY = {BUY: "Cheaper than it sells for, backed by real sales",
            CHECK: "Could be a good buy: one thing needs your eyes first",
            WATCH: "Not a Buy yet: here is the price that would make it one",
            PASS: "Not worth it, with the reason"}
TICKS = 24


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


# -- the rail ---------------------------------------------------------------------------------------------------------

def rail(counts: dict, active: str | None, href, compact: bool = False) -> str:
    """Four lanes with live counts. Clicking a lane shows only it; clicking the lane you are on shows everything again.
    href(code) -> the link for that lane."""
    lanes = []
    for code in (BUY, CHECK, WATCH, PASS):
        on = active == code
        lanes.append(f'<a class="lane l-{LANE[code]}{" on" if on else ""}" href="{e(href(code, on))}"'
                     f'{" aria-current=\"true\"" if on else ""} data-tip="{e(LABEL[code] + ": " + LANE_WHY[code])}">'
                     f'<span class="lane-g" aria-hidden="true"></span><span class="lane-w">{e(LABEL[code])}</span>'
                     f'<b class="lane-n">{counts.get(code, 0):,}</b>'
                     + ("" if compact else f'<small class="lane-why">{e(LANE_WHY[code])}</small>') + '</a>')
    return f'<nav class="rail{" compact" if compact else ""}" aria-label="Decisions">{"".join(lanes)}</nav>'


# -- what to check ----------------------------------------------------------------------------------------------------

_EDITION = re.compile(r"1st|unlimited|limited|shadowless", re.I)


def check_point(d: dict, v: Verdict) -> str:
    """The one thing a 2x Check is about, in two or three words."""
    kinds = set(d.get("kinds") or [])
    if "LOT_UNDERPRICED" in kinds:
        return "Card list and condition"
    if "MISIDENTIFIED" in kinds:
        return "Title and card disagree"
    labels = {s.get("label") or "" for s in d.get("scenarios") or []}
    parts = [set(lab.split(" · ")[i] if len(lab.split(" · ")) > i else "" for lab in labels) for i in range(4)]
    eds = {m.group(0).lower() for lab in labels for m in [_EDITION.search(lab)] if m}
    if len(eds) > 1 or (eds and any(not _EDITION.search(lab) for lab in labels)):
        return "Edition not stated"
    if len(labels) > 1 and len(parts[1]) > 1:
        return "Rarity not stated"
    if len(labels) > 1:
        return "Which version?"
    return "Photos vs title"


# -- the card ---------------------------------------------------------------------------------------------------------

def _value(v: Verdict) -> float | None:
    top = v.top or {}
    val = top.get("resale_mid")
    return float(val) if val else None


def _basis(v: Verdict) -> str:
    top = v.top or {}
    n = top.get("n_sales") or 0
    if top.get("basis") == "sales" and n:
        return f"Based on {n} recent sale{'s' if n != 1 else ''}"
    if top.get("basis") == "reference":
        return "From price guides, no real sales yet"
    return "No sales to compare with yet"


def value_bar(price: float | None, value: float | None, pay: float | None, M, code: str) -> str:
    """Price against value on one scale. The fill between them is the saving (orange) or the overpay (grey)."""
    if not price or not value:
        return ""
    hi = max(price, value, pay or 0) * 1.12
    x = lambda a: max(0.0, min(100.0, a / hi * 100))
    lo_, hi_ = sorted((x(price), x(value)))
    under = price <= value
    tip = f"Total to you {M(price)} · usually sells for {M(value)}" + (f" · worth paying up to {M(pay)}" if pay else "")
    return (f'<div class="vbar {"under" if under else "over"}" role="img" aria-label="{e(tip)}" data-tip="{e(tip)}">'
            f'<span class="vb-fill" style="left:{lo_:.1f}%;width:{hi_ - lo_:.1f}%"></span>'
            f'<span class="vb-price" style="left:{x(price):.1f}%"><i></i><small>{e(M(price))}</small></span>'
            f'<span class="vb-value" style="left:{x(value):.1f}%"><i></i><small>value {e(M(value))}</small></span>'
            + (f'<span class="vb-pay" style="left:{x(pay):.1f}%" aria-hidden="true"></span>' if pay else "") + '</div>')


def target_ring(price: float | None, target: float | None, M) -> str:
    """How close the ask is to a Buy price: the filled share of the ring is target / price."""
    if not price or not target:
        return ""                     # the basis line already says there are no sales to set a buy price from
    share = max(0.0, min(1.0, target / price))
    on = round(share * TICKS)
    ticks = "".join(f'<i class="t{" on" if i < on else ""}" style="--i:{i}"></i>' for i in range(TICKS))
    return (f'<div class="target" data-tip="{e(f"The ask is {share:.0%} of the way to a Buy price")}">'
            f'<span class="ring" role="img" aria-label="{e(f"{share:.0%} of the way to a Buy price")}">{ticks}<b>{share:.0%}</b></span>'
            f'<span><b>Buy at {e(M(target))} or less</b><small>now {e(M(price))}</small></span></div>')


def deal_card(key: str, d: dict, v: Verdict, cards, M, now: datetime, photos: dict | None = None, since: str | None = None,
              spark: str = "") -> str:
    lot = "LOT" in (d.get("kinds") or [])
    top = v.top or {}
    name = d.get("title") or key
    pc = _passcode(top)
    c = cards.card(pc) if (pc and cards is not None and not lot) else None
    if c and d.get("printing_id"):
        name = c.get("name_en") or name
    likely = _likely(d) or top
    sub = (" · ".join(likely["label"].split(" · ")[:3]) + ("" if d.get("printing_id") else " (probably)")
           if likely and likely.get("label") and not lot else "")
    price, value = d.get("all_in_home"), _value(v)
    pay = top.get("max_buy") if top.get("basis") == "sales" else None
    # A value is only shown where it means something: never "under value" on a Skip (it is skipped because the low
    # price is not to be trusted), and not when the title and the card disagree (the value is another card's).
    if "MISIDENTIFIED" in (d.get("kinds") or []) or (v.code == PASS and price and value and price < value):
        value = pay = None
    if price and value:
        delta = value - price
        big = f'{M(abs(delta))} {"under" if delta >= 0 else "over"} value'
    elif price:
        big = f'{M(price)} total'
    else:
        big = ""
    href = f'/opp/{quote(key, safe="")}'
    iid = item_id_of(key, d)
    from .ui_home import _listing_art, img
    if iid and (photos or {}).get(iid):
        art = lphoto(iid, 0, "l", d.get("title") or "", cls="dphoto")
        ref = _listing_art(d)
        if ref and not lot:
            art += (f'<span class="refart" data-tip="What this version looks like (reference picture, not the listing)">'
                    f'{img(ref, "small", "Reference picture")}</span>')
    else:
        art = _art(d, v, "large", cls="card dphoto")
    lane = LANE[v.code]
    chip = f'<span class="cpoint">{e(check_point(d, v))}</span>' if v.code == CHECK else ""
    extra = target_ring(price, top.get("max_buy"), M) if v.code == WATCH and not lot else ""
    more = (f'<details class="dmore"><summary>Details</summary><div class="dm-body">{listing_icons(d, now)}'
            f'{f"<p class=dm-sub>{e(sub)}</p>" if sub else ""}{spark or ""}'
            + (f'<p class="dm-line">Worth paying up to <b>{e(M(pay))}</b></p>' if pay else "")
            + f'<p class="dm-line"><a href="{href}">Open the full listing →</a></p></div></details>')
    return (f'<article class="deal d-{lane}" data-v="{v.code}"{f" data-since={chr(34)}{e(since)}{chr(34)}" if since else ""}>'
            f'<a class="dart" href="{href}" tabindex="-1">{art}{chip}<span class="sweep"></span></a>'
            f'<div class="dbody"><div class="dhead"><span class="dword">{e(LABEL[v.code])}</span>'
            f'{f"<span class=dnum>{e(big)}</span>" if big else ""}'
            f'{"<small class=dif>if it is this version</small>" if v.code == CHECK and price and value else ""}</div>'
            f'<a class="dtitle" href="{href}">{e(name)}</a>{f"<span class=dsub>{e(sub)}</span>" if sub else ""}'
            f'{value_bar(price, value, pay, M, v.code)}'
            f'<p class="dwhy">{e(v.reason)}</p>{extra}<p class="dbasis">{e(_basis(v))}</p>{more}</div></article>')


def first_seen(st, keys) -> dict[str, str]:
    keys = list(keys)
    out: dict[str, str] = {}
    try:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            out.update(st.db.execute(f"SELECT key, first_seen FROM listing WHERE key IN ({','.join('?' * len(chunk))})", chunk).fetchall())
    except Exception:
        pass
    return {k: v for k, v in out.items() if v}


# -- look -------------------------------------------------------------------------------------------------------------

DECIDE_CSS = """
:root{--bg:#e9f1f6;--surface:#ffffff;--glass:rgba(255,255,255,.78);--glass-line:rgba(255,255,255,.95);--fg:#10222e;--fg2:#3c4f5c;
--muted:#56697a;--line:#d6e2ea;--axis:#b9cad6;--soft:#eef4f8;--accent:#a64805;--orange:#d9660f;--orange-soft:#fde6d2;
--good:#1c6b48;--good-text:#1c6b48;--buy:#1c6b48;--check:#a64805;--watch:#365c74;--skip:#5a666e;--track:#dbe6ed;
--shadow:0 1px 2px rgba(16,34,46,.06),0 10px 30px rgba(16,34,46,.08);--r-lg:24px;--r-md:16px}
@media (prefers-color-scheme:dark){:root[data-theme="auto"]{color-scheme:dark;--bg:#0c1820;--surface:#15232d;--glass:rgba(21,35,45,.82);
--glass-line:rgba(255,255,255,.06);--fg:#e8f0f5;--fg2:#b8c7d0;--muted:#93a5b0;--line:#263844;--axis:#35495a;--soft:#1b2c37;--accent:#ffa65e;
--orange:#f28b38;--orange-soft:#3a2a1d;--good:#72d6a6;--good-text:#72d6a6;--buy:#72d6a6;--check:#ffa65e;--watch:#9fc6de;--skip:#a2adb4;
--track:#2a3a45;--shadow:0 1px 2px rgba(0,0,0,.3),0 12px 32px rgba(0,0,0,.35)}}
body{background:var(--bg) radial-gradient(1200px 500px at 15% -10%,color-mix(in srgb,var(--surface) 70%,transparent),transparent 70%) no-repeat}
header.top{background:var(--glass);border-bottom:1px solid var(--line)}
nav.main a.on{box-shadow:inset 0 -2px 0 var(--orange)}
.rail{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;padding:6px;margin:4px 0 16px;background:var(--glass);
border:1px solid var(--glass-line);border-radius:999px;box-shadow:var(--shadow);backdrop-filter:blur(10px)}
.rail .lane{display:grid;grid-template-columns:auto 1fr auto;grid-template-rows:auto auto;column-gap:10px;align-items:center;
padding:10px 18px;border-radius:999px;color:var(--fg);text-decoration:none;transition:background-color .2s ease-out,transform .2s ease-out}
.rail .lane:hover{background:var(--soft)}.rail .lane.on{background:var(--surface);box-shadow:inset 0 0 0 2px var(--lane)}
.rail .lane-w{font-weight:750;font-size:15px;white-space:nowrap}.rail .lane-n{font-size:20px;font-variant-numeric:tabular-nums;color:var(--lane)}
.rail .lane-why{grid-column:2/4;font-size:12px;color:var(--muted);line-height:1.3}
.rail .lane-g{grid-row:1/3;width:30px;height:30px;border-radius:50%;background:color-mix(in srgb,var(--lane) 14%,transparent);position:relative;overflow:hidden}
.rail .lane-g::after{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--lane)}
.l-buy{--lane:var(--buy)}.l-check{--lane:var(--check)}.l-watch{--lane:var(--watch)}.l-skip{--lane:var(--skip)}
.l-buy .lane-g::after{inset:13px 6px;border-radius:3px}.l-watch .lane-g::after{inset:7px;background:none;border:3px solid var(--lane)}
.l-check .lane-g::after{inset:6px 13px;border-radius:3px;transform:skewX(-14deg)}.l-skip .lane-g::after{opacity:.45}
.rail.compact .lane{grid-template-rows:auto;padding:8px 14px}
@media(max-width:760px){.rail{grid-template-columns:repeat(2,minmax(0,1fr));border-radius:var(--r-lg)}.rail .lane{border-radius:var(--r-md);padding:8px 12px}
.rail .lane-why{display:none}.rail .lane-g{grid-row:1;width:24px;height:24px}.rail .lane-g::after{inset:7px}}
.fsecs{display:flex;flex-wrap:wrap;gap:10px 18px;margin:0 0 14px}
.fsec{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 6px 6px 14px;background:var(--glass);border:1px solid var(--glass-line);
border-radius:999px;box-shadow:var(--shadow)}
.fsec h3{margin:0;white-space:nowrap;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:700}
.fsec .fchip{border-radius:999px}
.fchip{display:inline-flex;gap:6px;align-items:center;padding:6px 12px;border-radius:999px;color:var(--fg2);text-decoration:none;font-size:13px;
font-weight:600;border:1px solid transparent}.fchip:hover{background:var(--soft)}.fchip.on{background:var(--fg);color:var(--surface)}
@media(max-width:760px){.fsec{border-radius:var(--r-md);flex-wrap:nowrap;overflow-x:auto;max-width:100%;scrollbar-width:none}.fsec .fchip{white-space:nowrap}}
.deals{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(420px,100%),1fr));gap:14px}
.deal{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px;padding:14px;background:var(--glass);border:1px solid var(--glass-line);
border-radius:var(--r-lg);box-shadow:var(--shadow);position:relative;transition:opacity .2s ease-out}
.dart{position:relative;display:block;border-radius:var(--r-md);overflow:hidden;background:var(--soft);aspect-ratio:3/4;align-self:start}
.dart img.dphoto,.dart .dphoto{width:100%;height:100%;object-fit:cover;display:block;border-radius:0}
.dart .refart{right:6px;bottom:6px;width:30px;height:43px}.d-check .dart .refart{bottom:auto;top:6px}
.dif{flex-basis:100%;margin-top:-4px;font-size:12px;color:var(--muted)}
.dbody{display:flex;flex-direction:column;gap:6px;min-width:0}
.dhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.dword{font-weight:800;font-size:22px;letter-spacing:-.01em;color:var(--lane)}
.dnum{font-weight:800;font-size:22px;font-variant-numeric:tabular-nums;color:var(--fg)}
.d-buy{--lane:var(--buy)}.d-check{--lane:var(--check)}.d-watch{--lane:var(--watch)}.d-skip{--lane:var(--skip)}
.dtitle{font-weight:650;color:var(--fg);text-decoration:none;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dtitle:hover{text-decoration:underline}.dsub{font-size:12px;color:var(--muted)}
.dwhy{margin:2px 0 0;color:var(--fg2);font-size:14px;line-height:1.4}.dbasis{margin:0;font-size:12px;color:var(--muted)}
.vbar{position:relative;height:10px;margin:14px 0 22px;border-radius:999px;background:var(--track)}
.vb-fill{position:absolute;top:0;bottom:0;border-radius:999px;background:var(--orange);transform-origin:left center}
.vbar.over .vb-fill{background:repeating-linear-gradient(135deg,var(--axis),var(--axis) 4px,transparent 4px,transparent 8px);transform-origin:right center}
.vb-price,.vb-value,.vb-pay{position:absolute;top:50%}
.vb-price i{position:absolute;left:-7px;top:-7px;width:14px;height:14px;border-radius:50%;background:var(--fg);box-shadow:0 0 0 3px var(--surface)}
.vb-value i{position:absolute;left:-1.5px;top:-11px;width:3px;height:22px;border-radius:2px;background:var(--fg2)}
.vb-pay{top:-4px;bottom:-4px;width:2px;margin-left:-1px;background:var(--buy);opacity:.8}
.vb-price small,.vb-value small{position:absolute;top:10px;white-space:nowrap;font-size:11px;font-weight:650;color:var(--fg2);transform:translateX(-50%)}
.vb-value small{top:-28px}
.cpoint{position:absolute;left:8px;right:8px;bottom:8px;padding:5px 10px;border-radius:999px;background:var(--surface);color:var(--check);
font-size:12px;font-weight:750;text-align:center;box-shadow:0 0 0 2px var(--check)}
.sweep{position:absolute;inset:0;pointer-events:none;opacity:0;background:linear-gradient(100deg,transparent 30%,rgba(255,255,255,.7) 50%,transparent 70%)}
.target{display:flex;gap:12px;align-items:center;margin:2px 0}.target b{display:block}.target small{color:var(--muted);font-size:12px}
.target.none{font-size:13px;color:var(--muted);margin:0}
.ring{position:relative;width:48px;height:48px;flex:none;display:inline-flex;align-items:center;justify-content:center}
.ring b{font-size:11px;font-variant-numeric:tabular-nums;color:var(--watch)}
.ring .t{position:absolute;left:50%;top:50%;width:3px;height:7px;margin:-3.5px 0 0 -1.5px;border-radius:2px;background:var(--track);
transform:rotate(calc(var(--i)*15deg)) translateY(-20px)}.ring .t.on{background:var(--watch)}
.d-skip{opacity:.62}.d-skip:hover,.d-skip:focus-within{opacity:1}
.dmore summary{font-size:12px;color:var(--muted);list-style:none;display:inline-flex;gap:4px;align-items:center}
.dmore summary::before{content:"+";font-weight:700}.dmore[open] summary::before{content:"–"}
.dm-body{display:flex;flex-direction:column;gap:6px;padding:8px 0 0}.dm-sub,.dm-line{margin:0;font-size:12px;color:var(--fg2)}
.deal.new::after{content:"";position:absolute;inset:-1px;border-radius:inherit;box-shadow:0 0 0 2px var(--orange),0 0 24px color-mix(in srgb,var(--orange) 45%,transparent);
opacity:0;pointer-events:none}
@media(max-width:560px){.deal{grid-template-columns:104px minmax(0,1fr);gap:12px;padding:12px}.dword,.dnum{font-size:19px}.cpoint{left:4px;right:4px;bottom:4px;padding:3px 4px;font-size:10px;line-height:1.2}}
/* motion: once, on entering view; transform and opacity only */
.d-buy.in .vb-fill{animation:cm-fill .32s ease-out backwards}.deal.new.in::after{animation:cm-glow .35s ease-out .32s backwards}
.d-check.in .sweep{animation:cm-sweep .35s ease-out backwards}.d-check.in .cpoint{animation:cm-land .2s ease-out .3s backwards}
.d-watch.in .ring .t.on{animation:cm-tick .15s ease-out backwards;animation-delay:calc(var(--i)*8ms)}
.d-skip.in{animation:cm-settle .25s ease-out backwards}
@keyframes cm-fill{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes cm-glow{0%{opacity:0}40%{opacity:1}100%{opacity:0}}
@keyframes cm-sweep{0%{opacity:1;transform:translateX(-100%)}100%{opacity:0;transform:translateX(100%)}}
@keyframes cm-land{from{opacity:.4;transform:scale(.94)}to{opacity:1;transform:scale(1)}}
@keyframes cm-tick{from{opacity:0}to{opacity:1}}
@keyframes cm-settle{from{opacity:1;transform:translateY(-4px)}to{opacity:.62;transform:none}}
@media (prefers-reduced-motion:reduce){.deal.in *,.deal.in,.deal.in::after{animation:none!important}.rail .lane{transition:none}}
.home-h{display:flex;align-items:baseline;justify-content:space-between;margin:22px 0 10px}.home-h h2{font-size:19px;margin:0}
.home-h a{font-size:13px}
"""

DECIDE_JS = r"""
(function(){var cards=[].slice.call(document.querySelectorAll('.deal'));if(!cards.length)return;
var prev=null;try{prev=localStorage.getItem('cm-last-visit')}catch(x){}
cards.forEach(function(c){var s=c.getAttribute('data-since');if(prev&&s&&c.getAttribute('data-v')==='BUY'&&s>prev)c.classList.add('new')});
try{localStorage.setItem('cm-last-visit',new Date().toISOString())}catch(x){}
if(!('IntersectionObserver' in window)){cards.forEach(function(c){c.classList.add('in')});return}
var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target)}})},
{threshold:.35});cards.forEach(function(c){io.observe(c)})})();
"""
