"""Decisions, made visible: the decision rail and the listing card. Presentation only; every number and verdict comes
from ui_listing.verdict (nothing here decides or invents a price).

Card, top to bottom, in the order you read it:
  photo        the seller's own photo (the thing you would buy), reference art in the corner
  title        the identified card: name, then set · number · version                       what is it?
  info line    total to you · value · under/over · basis ("Based on 14 sales")             what is it worth?
  track        a slim price-vs-value track (only when there is a value)                      where does the price sit?
  decision     a quiet pill with its icon (Buy / 2x Check / Watch / Skip), plus what to check  what should I do?
  why          the verdict's one-line reason                                                  why?
Everything else is folded under "Details".

Motion (transform/opacity only, cubic-bezier(.2,.8,.2,1), 150–400 ms, no loops, off under prefers-reduced-motion):
  cards rise 8 px into view, staggered 30 ms; hover lifts 2 px and zooms the photo 3 %;
  the price marker slides into place; the 2x Check chip gets one shimmer; the rail's active pill slides in and the
  counts tick up once; tooltips fade in.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import quote

from .ui_listing import BUY, CHECK, LABEL, PASS, WATCH, Verdict, _art, _likely, _passcode, item_id_of, listing_icons, lphoto

LANE = {BUY: "buy", CHECK: "check", WATCH: "watch", PASS: "skip"}
LANE_WHY = {BUY: "Cheaper than it sells for, backed by real sales",
            CHECK: "Could be a good buy: one thing needs a second look first",
            WATCH: "Not a Buy yet: shows the price that would make it one",
            PASS: "Not worth it, with the reason"}


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _icon(code: str) -> str:
    from .ui_icons import SVG_USE
    return SVG_USE(LANE[code])                    # the original sprite icons: buy, check, watch, skip


# -- the rail ---------------------------------------------------------------------------------------------------------

def rail(counts: dict, active: str | None, href, compact: bool = False) -> str:
    """Four small pills: icon, label, live count. Clicking one shows only it; clicking the one you are on shows all.
    href(code, on) -> the link."""
    pills = []
    for code in (BUY, CHECK, WATCH, PASS):
        on = active == code
        pills.append(f'<a class="lane l-{LANE[code]}{" on" if on else ""}" href="{e(href(code, on))}"'
                     f'{" aria-current=\"true\"" if on else ""} data-tip="{e(LABEL[code] + ": " + LANE_WHY[code])}">'
                     f'<span class="lane-i">{_icon(code)}</span><span class="lane-w">{e(LABEL[code])}</span>'
                     f'<b class="lane-n" data-n="{counts.get(code, 0)}">{counts.get(code, 0):,}</b></a>')
    return (f'<nav class="rail{" compact" if compact else ""}" aria-label="Decisions"><span class="rail-ind" aria-hidden="true"></span>'
            f'{"".join(pills)}</nav>')


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

def _basis(v: Verdict) -> str:
    top = v.top or {}
    n = top.get("n_sales") or 0
    if top.get("basis") == "sales" and n:
        return f"Based on {n} sale{'s' if n != 1 else ''}"
    if top.get("basis") == "reference":
        return "From price guides"
    return "No sales yet"


def price_track(price: float | None, value: float | None, M) -> str:
    """Where the asking price sits against the value: a slim grey track, a teal marker (the price), a thin tick (value)."""
    if not price or not value:
        return ""
    hi = max(price, value) * 1.15
    x = lambda a: max(0.0, min(100.0, a / hi * 100))
    tip = f"Total to you {M(price)}\nUsually sells for {M(value)}"
    return (f'<div class="track{" under" if price <= value else " over"}" role="img" aria-label="{e(tip)}" data-tip="{e(tip)}">'
            f'<span class="tk-v" style="left:{x(value):.1f}%"></span>'
            f'<span class="tk-m" style="--x:{x(price):.1f}%"><i></i></span></div>')


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
    price, value = d.get("all_in_home"), (float(top["resale_mid"]) if top.get("resale_mid") else None)
    # A value is shown only where it means something: never on a Skip with a suspiciously low price (it is skipped
    # because that price is not to be trusted), nor when the title and the card disagree (the value is another card's).
    if "MISIDENTIFIED" in (d.get("kinds") or []) or (v.code == PASS and price and value and price < value):
        value = None
    ship = d.get("shipping")
    info = [f'<b class="i-price">{e(M(price))}</b>'
            + ('<span class="i-ship">free shipping</span>' if ship not in (None, "", "None") and float(ship) == 0 else "")]
    if value:
        delta = value - price if price else None
        info.append(f'<span>value {e(M(value))}</span>')
        if delta is not None:
            info.append(f'<span class="i-delta {"up" if delta >= 0 else "down"}">{e(M(abs(delta)))} {"under" if delta >= 0 else "over"}</span>')
    target = top.get("max_buy") if v.code == WATCH and not lot else None
    if target:
        info.append(f'<span>Buy at {e(M(target))} or less</span>')
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
    pill = (f'<span class="dpill p-{lane}" data-tip="{e(LABEL[v.code] + ": " + LANE_WHY[v.code])}">{_icon(v.code)}{e(LABEL[v.code])}</span>'
            + (f'<span class="cpoint">{e(check_point(d, v))}</span>' if v.code == CHECK else ""))
    more = (f'<details class="dmore"><summary>Details</summary><div class="dm-body">{listing_icons(d, now)}{spark or ""}'
            + (f'<p class="dm-line">Worth paying up to <b>{e(M(top["max_buy"]))}</b></p>' if top.get("max_buy") and top.get("basis") == "sales" else "")
            + f'<p class="dm-line"><a href="{href}">Open the full listing</a></p></div></details>')
    return (f'<article class="deal d-{lane}" data-v="{v.code}"{f" data-since={chr(34)}{e(since)}{chr(34)}" if since else ""}>'
            f'<a class="dart" href="{href}" tabindex="-1">{art}</a>'
            f'<div class="dbody"><a class="dtitle" href="{href}">{e(name)}</a>{f"<span class=dsub>{e(sub)}</span>" if sub else ""}'
            f'<p class="dinfo">{"".join(info)}<span class="i-basis">{e(_basis(v))}</span></p>'
            f'{price_track(price, value, M)}<div class="dpills">{pill}</div>'
            f'<p class="dwhy">{e(v.reason)}</p>{more}</div></article>')


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
:root{--bg:#FBFDFE;--surface:#FFFFFF;--line:#E6EEF2;--axis:#D3DFE6;--soft:#F2F7FA;--fg:#1F2A33;--fg2:#3F4E59;--muted:#5F6F7A;
--sky:#8EC9E8;--sky-text:#2B7BA6;--teal:#1E9E9A;--teal-deep:#137C7A;--slate:#4F6378;--grey:#66737C;
--accent:var(--teal-deep);--good:var(--teal);--good-text:var(--teal-deep);--warn:var(--slate);--s1:var(--teal);--s2:var(--sky);--s3:var(--teal-deep);--s4:var(--slate);
--track:#E6EEF2;--shadow:0 1px 2px rgba(31,42,51,.04),0 4px 16px rgba(31,42,51,.05);--shadow-hi:0 2px 4px rgba(31,42,51,.05),0 12px 32px rgba(31,42,51,.09);
--ease:cubic-bezier(.2,.8,.2,1);--r:14px}
@media (prefers-color-scheme:dark){:root[data-theme="auto"]{color-scheme:dark;--bg:#0F171C;--surface:#152027;--line:#23323B;--axis:#2E414C;
--soft:#1A2830;--fg:#E6EEF2;--fg2:#C2CFD7;--muted:#93A4AE;--sky:#8EC9E8;--sky-text:#8EC9E8;--teal:#4CC9C3;--teal-deep:#4CC9C3;--slate:#A9B8C8;
--grey:#9AA6AE;--track:#23323B;--shadow:0 1px 2px rgba(0,0,0,.25),0 4px 16px rgba(0,0,0,.2);--shadow-hi:0 2px 4px rgba(0,0,0,.3),0 12px 32px rgba(0,0,0,.35)}}
body{background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,"Noto Sans TC","Noto Sans JP",sans-serif;
-webkit-font-smoothing:antialiased}
header.top{background:color-mix(in srgb,var(--surface) 88%,transparent);border-bottom:1px solid var(--line)}
nav.main a.on{background:var(--soft);box-shadow:inset 0 -2px 0 var(--teal)}
form.search button{background:var(--fg);border-color:var(--fg)}
/* rail: four slim pills */
.rail{position:relative;display:inline-flex;flex-wrap:wrap;gap:4px;padding:4px;margin:8px 0 16px;background:var(--soft);border:1px solid var(--line);
border-radius:999px}
.rail .lane{position:relative;z-index:1;display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;border-radius:999px;color:var(--fg2);
text-decoration:none;font-size:14px;font-weight:500;transition:color .2s var(--ease)}
.rail .lane:hover{color:var(--fg)}.rail .lane.on{color:var(--fg);font-weight:600}
.rail .lane-i{display:inline-flex;width:18px;height:18px;color:var(--lane)}.rail .lane-i .svi{width:18px;height:18px}
.rail .lane-n{font-weight:600;font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px}.rail .lane.on .lane-n{color:var(--lane)}
.rail-ind{position:absolute;z-index:0;top:4px;left:0;height:36px;width:0;border-radius:999px;background:var(--surface);box-shadow:var(--shadow);opacity:0;
transition:transform .32s var(--ease),width .32s var(--ease),opacity .2s var(--ease)}
.rail.js .rail-ind{opacity:1}.rail:not(.js) .lane.on{background:var(--surface);box-shadow:var(--shadow)}
.l-buy,.p-buy{--lane:var(--teal-deep)}.l-check,.p-check{--lane:var(--slate)}.l-watch,.p-watch{--lane:var(--sky-text)}.l-skip,.p-skip{--lane:var(--grey)}
@media(max-width:760px){.rail{display:grid;grid-template-columns:1fr 1fr;border-radius:18px;width:100%}.rail .lane{justify-content:flex-start}
.rail .lane-n{margin-left:auto}}
/* filter sections */
.fsecs{display:flex;flex-wrap:wrap;gap:8px 24px;margin:0 0 24px;align-items:center}
.fsec{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.fsec h3{margin:0 8px 0 0;font-size:12px;font-weight:500;color:var(--muted);white-space:nowrap}
.fchip{display:inline-flex;align-items:center;height:32px;padding:0 12px;border-radius:999px;color:var(--fg2);text-decoration:none;font-size:13px;
font-weight:500;border:1px solid var(--line);background:var(--surface);transition:background-color .2s var(--ease),border-color .2s var(--ease)}
.fchip:hover{border-color:var(--axis)}.fchip.on{background:var(--fg);border-color:var(--fg);color:var(--surface)}
@media(max-width:760px){.fsec{flex-wrap:nowrap;overflow-x:auto;max-width:100%;scrollbar-width:none}.fchip{white-space:nowrap}}
/* cards */
.deals{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(440px,100%),1fr));gap:16px}
.deal{display:grid;grid-template-columns:112px minmax(0,1fr);gap:16px;padding:16px;background:var(--surface);border:1px solid var(--line);
border-radius:var(--r);box-shadow:var(--shadow);transition:transform .26s var(--ease),box-shadow .26s var(--ease),opacity .26s var(--ease);
transition-delay:var(--d,0ms)}
.deal:hover{transform:translateY(-2px);box-shadow:var(--shadow-hi)}
.dart{position:relative;display:block;border-radius:10px;overflow:hidden;background:var(--soft);aspect-ratio:3/4;align-self:start}
.dart img.dphoto,.dart .dphoto{width:100%;height:100%;object-fit:cover;display:block;border-radius:0;transition:transform .4s var(--ease)}
.deal:hover .dphoto{transform:scale(1.03)}
.dart .refart{right:4px;bottom:4px;width:26px;height:37px;border-radius:3px}
.dbody{display:flex;flex-direction:column;gap:8px;min-width:0}
.dtitle{font-size:17px;font-weight:600;line-height:1.3;color:var(--fg);text-decoration:none;letter-spacing:-.01em;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dtitle:hover{color:var(--teal-deep)}.dsub{font-size:13px;font-weight:500;color:var(--muted);margin-top:-4px}
.dinfo{margin:0;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;font-size:14px;font-weight:500;color:var(--fg2);font-variant-numeric:tabular-nums}
.i-price{font-size:15px;font-weight:600;color:var(--fg)}.i-ship{font-size:12px;color:var(--muted);margin-left:6px}
.i-delta.up{color:var(--teal-deep);font-weight:600}.i-delta.down{color:var(--grey)}
.i-basis{font-size:12px;color:var(--muted)}
.track{position:relative;height:4px;margin:4px 0;border-radius:2px;background:var(--track)}
.tk-v{position:absolute;top:-4px;width:2px;height:12px;margin-left:-1px;border-radius:1px;background:var(--fg2);opacity:.55}
.tk-m{position:absolute;inset:0;transform:translateX(var(--x));pointer-events:none}
.tk-m i{position:absolute;left:-6px;top:-4px;width:12px;height:12px;border-radius:50%;background:var(--teal);box-shadow:0 0 0 3px var(--surface)}
.track.over .tk-m i{background:var(--grey)}
.dpills{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.dpill{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px 0 8px;border-radius:999px;font-size:12px;font-weight:600;color:var(--lane);
background:color-mix(in srgb,var(--lane) 10%,var(--surface));border:1px solid color-mix(in srgb,var(--lane) 22%,transparent)}
.dpill .svi{width:14px;height:14px}
.cpoint{position:relative;overflow:hidden;display:inline-flex;align-items:center;height:26px;padding:0 10px;border-radius:999px;font-size:12px;font-weight:500;
color:var(--fg2);background:var(--soft);border:1px dashed var(--axis)}
.cpoint::after{content:"";position:absolute;inset:0;transform:translateX(-110%);background:linear-gradient(100deg,transparent 20%,
color-mix(in srgb,var(--sky) 55%,transparent) 50%,transparent 80%)}
.dwhy{margin:0;font-size:13px;line-height:1.45;color:var(--muted)}
.d-skip .dtitle{color:var(--fg2)}.d-skip .dart{opacity:.7}
.dmore summary{font-size:12px;color:var(--muted);list-style:none;cursor:pointer}.dmore summary::-webkit-details-marker{display:none}
.dmore summary::after{content:" ›";}.dmore[open] summary::after{content:" ‹"}
.dm-body{display:flex;flex-direction:column;gap:8px;padding:8px 0 0}.dm-line{margin:0;font-size:12px;color:var(--fg2)}
@media(max-width:560px){.deal{grid-template-columns:88px minmax(0,1fr);gap:12px;padding:12px}.dtitle{font-size:16px}}
.home-h{display:flex;align-items:baseline;justify-content:space-between;margin:32px 0 12px}.home-h h2{font-size:17px;font-weight:600;margin:0}
.home-h a{font-size:13px;color:var(--teal-deep)}
.tip{animation:cm-tip .15s var(--ease)}
.chip.warn,.warnt{color:var(--slate)}.chip.warn{border-color:var(--axis)}.vbadge.warn .vi,.call.warn .ci,.ic.warnic{background:var(--slate);color:#fff}
/* motion: only when the script runs and motion is allowed (html.cm-motion) */
.cm-motion .deal:not(.in){opacity:0;transform:translateY(8px)}
.cm-motion .deal.in .tk-m{animation:cm-slide .4s var(--ease) calc(var(--d,0ms) + 120ms) backwards}
.cm-motion .deal.in .cpoint::after{animation:cm-shimmer .4s var(--ease) calc(var(--d,0ms) + 260ms) backwards}
@keyframes cm-slide{from{transform:translateX(0)}}
@keyframes cm-shimmer{from{transform:translateX(-110%)}to{transform:translateX(110%)}}
@keyframes cm-tip{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.deal,.dphoto,.rail-ind,.fchip,.rail .lane{transition:none!important}.tip,.tk-m,.cpoint::after{animation:none!important}
.deal:hover{transform:none}.deal:hover .dphoto{transform:none}}
"""

DECIDE_JS = r"""
(function(){var calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches,root=document.documentElement;
var rails=[].slice.call(document.querySelectorAll('.rail'));
rails.forEach(function(r){var on=r.querySelector('.lane.on'),ind=r.querySelector('.rail-ind');r.classList.add('js');
 if(!on||!ind){r.classList.remove('js');return}
 function place(el){ind.style.width=el.offsetWidth+'px';ind.style.top=el.offsetTop+'px';ind.style.transform='translateX('+el.offsetLeft+'px)'}
 var first=r.querySelector('.lane');if(!calm&&first!==on){place(first);ind.getBoundingClientRect();requestAnimationFrame(function(){place(on)})}else place(on);
 addEventListener('resize',function(){place(on)})});
if(!calm){[].slice.call(document.querySelectorAll('.lane-n[data-n]')).forEach(function(n){var to=+n.getAttribute('data-n');if(!to)return;
 var t0=null;function step(t){t0=t0||t;var k=Math.min(1,(t-t0)/400),v=Math.round(to*(1-Math.pow(1-k,3)));n.textContent=v.toLocaleString();
 if(k<1)requestAnimationFrame(step)}n.textContent='0';requestAnimationFrame(step)})}
var cards=[].slice.call(document.querySelectorAll('.deal'));if(!cards.length||calm||!('IntersectionObserver' in window))return;
root.classList.add('cm-motion');var batch=0,timer=null;
var io=new IntersectionObserver(function(es){es.forEach(function(en){if(!en.isIntersecting)return;var c=en.target;io.unobserve(c);
 c.style.setProperty('--d',(Math.min(batch++,8)*30)+'ms');c.classList.add('in');clearTimeout(timer);timer=setTimeout(function(){batch=0},120);
 setTimeout(function(){c.style.setProperty('--d','0ms')},700)})},{threshold:.15,rootMargin:'0px 0px -5% 0px'});
cards.forEach(function(c){io.observe(c)})})();
"""
