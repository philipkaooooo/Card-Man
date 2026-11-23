"""Now: the two things worth acting on, drawn big. Live asks under typical, and raws that pay for grading.

Same numbers as the market board's sections (`ui_market._actionable`), presented as a floor: the card floats
over a glass panel, the seller's own photo where the listing carries one and the official picture otherwise.
"""
from __future__ import annotations

from datetime import datetime

from . import trends
from .games import card_href
from .ui_home import Money, _tables, e
from .ui_market import ON_TAB, _actionable, _card_key, _name_of

NOW_CSS = """
.nz-zone{padding:4px 0 30px;background:
radial-gradient(900px 300px at 10% -60px, color-mix(in srgb,var(--teal) 8%, transparent), transparent 65%),
radial-gradient(700px 260px at 90% -40px, color-mix(in srgb,#8a6614 7%, transparent), transparent 70%)}
.nz-lead{color:var(--fg2);font-size:14px;max-width:72ch;margin:0 0 4px}
.nz-h{display:flex;align-items:baseline;gap:10px;margin:22px 0 2px;flex-wrap:wrap}
.nz-h h2{margin:0;font-size:20px;letter-spacing:-.01em}
.nz-h small{color:var(--muted);font-size:12.5px;font-variant-numeric:tabular-nums}
.nz-h a{margin-left:auto;font-size:12.5px;font-weight:700;color:var(--fg2);text-decoration:none}
.nz-h a:hover{color:var(--teal-deep)}
.nz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:16px;margin:12px 0 8px}
.nz-tile{position:relative;border-radius:20px;overflow:hidden;border:1px solid var(--line);
background:linear-gradient(165deg,rgba(255,255,255,.55),rgba(255,255,255,.15)),color-mix(in srgb,var(--surface) 55%,transparent);
backdrop-filter:blur(14px) saturate(1.25);-webkit-backdrop-filter:blur(14px) saturate(1.25);box-shadow:var(--shadow);
transition:transform .18s,box-shadow .18s}
.nz-tile:hover{transform:translateY(-3px);box-shadow:var(--shadow-hi)}
.nz-frame{position:relative;display:flex;justify-content:center;padding:20px 16px 8px;
background:radial-gradient(120% 90% at 50% 0%,color-mix(in srgb,var(--teal) 9%, transparent),transparent 70%)}
.nz-tile.grade .nz-frame{background:radial-gradient(120% 90% at 50% 0%,color-mix(in srgb,#8a6614 10%, transparent),transparent 70%)}
.nz-art{width:158px;height:222px;object-fit:contain;filter:drop-shadow(0 16px 20px rgba(15,30,45,.22));
transition:transform .2s var(--ease)}
.nz-tile:hover .nz-art{transform:translateY(-4px) rotate(-.6deg)}
.nz-art.ph{display:flex;align-items:center;justify-content:center;font-size:28px;color:var(--muted);
border:1px dashed var(--line);border-radius:10px;background:var(--soft);filter:none}
.nz-tag{position:absolute;top:12px;left:12px;font-size:10.5px;font-weight:800;letter-spacing:.06em;
text-transform:uppercase;padding:3px 9px;border-radius:999px;color:#fff;background:var(--teal);box-shadow:var(--shadow)}
.nz-tag.grade{background:#8a6614}
.nz-tag.wait{background:color-mix(in srgb,#8a6614 18%,var(--surface));color:#8a6614;box-shadow:none}
.nz-glass{margin:0 8px 8px;border-radius:14px;border:1px solid color-mix(in srgb,var(--line) 80%,transparent);
background:color-mix(in srgb,var(--surface) 80%,transparent);backdrop-filter:blur(8px);padding:10px 12px 11px}
.nz-name{display:block;font-weight:800;font-size:14.5px;color:var(--fg);text-decoration:none;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.nz-name:hover{color:var(--teal-deep)}
.nz-meta{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:1px 0 7px}
.nz-nums{display:grid;grid-template-columns:repeat(auto-fit,minmax(62px,1fr));gap:4px 8px}
.nz-nums b{display:block;font-size:14.5px;font-variant-numeric:tabular-nums;line-height:1.15;white-space:nowrap}
.nz-nums b.pos{color:var(--teal-deep)}
.nz-nums small{display:block;font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);font-weight:700}
.nz-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:9px;font-size:11px;color:var(--muted)}
.nz-cta{font-size:12px;font-weight:800;color:var(--teal-deep);text-decoration:none;white-space:nowrap}
.nz-cta:hover{text-decoration:underline}
.nz-cta.muted{color:var(--muted);font-weight:600}
.nz-empty{border:1px dashed var(--line);border-radius:16px;padding:18px;color:var(--fg2);font-size:14px;margin:12px 0 4px}
@media(max-width:640px){.nz-grid{grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.nz-art{width:118px;height:166px}}
"""


def _official(key: str | None) -> str | None:
    if not key:
        return None
    if key.startswith("pkm-"):
        return f"/pkm/img/pkm/{key[4:]}.png?size=small"
    return f"/img/card/{key}.jpg?size=small"


def _pace(rate: float | None) -> str:
    if not rate:
        return "no sales pace"
    n = f"{rate:.1f}" if rate < 10 else f"{rate:.0f}"
    return f"{n} copies/mo"


def _art(pid: str, items: dict, gkey: str, key: str | None, nm: str) -> str:
    lid = items.get((pid, gkey or "|"))
    off = _official(key)
    if lid:
        src = f"/img/listing/{e(lid)}/0.jpg?s=m"
        alt = f"{nm} - the listing's own photo"
        fb = f" onerror=\"this.onerror=null;this.src='{e(off)}'\"" if off else ""
        return f'<img class="nz-art" src="{src}" alt="{e(alt)}"{fb} loading="lazy" decoding="async">'
    if off:
        return f'<img class="nz-art" src="{off}" alt="{e(nm)}" loading="lazy" decoding="async">'
    return '<span class="nz-art ph" role="img" aria-label="No exact card yet">?</span>'


def _tile(pid: str, gkey: str, key: str | None, nm: str, label: str, tag: str, tag_cls: str, art: str,
          nums: str, proof: str, foot: str, cta: str, extra_cls: str = "") -> str:
    body = (f'<a class="nz-name" href="{e(card_href(key))}">{e(nm)}</a>' if key
            else f'<span class="nz-name">{e(nm)}</span>')
    return ('<article class="nz-tile' + extra_cls + '">'
            f'<div class="nz-frame">{art}<span class="nz-tag {tag_cls}">{e(tag)}</span></div>'
            '<div class="nz-glass">' + body
            + f'<div class="nz-meta">{e(label)}</div>'
            + f'<div class="nz-nums">{nums}</div>'
            + f'<div class="mkproof"><span class="pl">sold</span>{proof}</div>'
            + f'<div class="nz-foot"><span>{foot}</span>{cta}</div>'
            + '</div></article>')


def _num(v: str, lab: str, pos: bool = False) -> str:
    return f'<span><b class="{"pos" if pos else "n"}">{v}</b><small>{lab}</small></span>'


def view_now(st, now: datetime, cur: str, home: str, qs: dict, game: str = "ygo",
             cards=None, pkm=None) -> str:
    if "price_trend" not in _tables(st):
        return ('<h1>Now</h1><p class="empty">Nothing to act on until cards start selling. The engine writes the '
                'board at the end of every pass.</p>')
    M = Money(st, home, cur, now)
    rows = trends.board(st, game=game, sort="edge", min_sales=3, limit=200)
    A = _actionable(st, game, now, home, cur, rows)
    name_of = _name_of(cards, pkm)
    fair_of = A["fair_of"]

    name = {"ygo": "Yu-Gi-Oh!", "pkm": "Pokémon"}.get(game, game)
    tabs = ('<div class="mk-games" role="tablist" aria-label="Game">'
            + "".join(f'<a role="tab"{ON_TAB if g == game else ""} '
                      f'href="/game?{e(_q({"g": g, "back": "/market/now"}))}">{e(lab)}</a>'
                      for g, lab in (("ygo", "Yu-Gi-Oh!"), ("pkm", "Pokémon")))
            + '</div>')

    under = []
    for d in A["deals"]:
        pid, gkey = d["printing_id"], d["gkey"] or "|"
        nm = name_of(pid) or d["label"].split(" · ")[0]
        key = _card_key(pid)
        f, ask = fair_of(d), d["ask_low"]
        pct = (f - ask) / f * 100
        net = A["flip_profit"](ask, f)
        url = A["links"].get((pid, gkey))
        cta = (f'<a class="nz-cta" href="{e(url)}" target="_blank" rel="noreferrer noopener">Open the listing →</a>'
               if url else '<span class="nz-cta muted">Listing link expired</span>')
        under.append(_tile(pid, gkey, key, nm, d["label"], f"{pct:.0f}% under", "under",
                           _art(pid, A["items"], gkey, key, nm),
                           _num(M(ask), "ask") + _num(M(f), "typical") + _num(f"+{M(net)}", "after fees", True),
                           A["proof_sales"](pid), _pace(d["sales_month"]), cta))

    graded = []
    for kind, room, r, ask, net, psa in A["picks"]:
        pid = r["printing_id"]
        key = _card_key(pid)
        nm = name_of(pid) or r["label"].split(" · ")[0]
        rate = A["vol_per_month"](psa.get("vol") or "")
        per = _pace(rate) if rate else "rare"
        pop = (A["pcrows"].get(pid) or {}).get("pop") or {}
        psa_pop = pop.get("psa") or []
        popn = psa_pop[9] if len(psa_pop) >= 10 else None
        foot = f"PSA 10 {per}" + (f" · pop {popn:,}" if popn is not None else "")
        if kind == "now":
            all_in = A["k_buy"] * ask + A["fixed"]
            href = A["links"].get((pid, "|"))
            tag, cls = "buy now", "grade"
            nums = (_num(M(ask), "raw ask") + _num(M(all_in), "all-in")
                    + _num(f"+{M(room)}", "profit", True))
            cta = (f'<a class="nz-cta" href="{e(href)}" target="_blank" rel="noreferrer noopener">Open the raw listing →</a>'
                   if href else '<span class="nz-cta muted">Ask snapshot only</span>')
        else:
            tag, cls = "wait for", "wait"
            nums = (_num(M(ask), "pay at most") + _num(M(A["k_buy"] * ask + A["fixed"]), "all-in")
                    + _num(M(net), "PSA 10 nets"))
            cta = (f'<a class="nz-cta" href="{e(card_href(key))}">Watch the card →</a>' if key
                   else '<span class="nz-cta muted">—</span>')
        graded.append(_tile(pid, "|", key, nm, r["label"], tag, cls,
                            _art(pid, A["items"], "|", key, nm), nums, A["proof_sales"](pid), foot, cta, " grade"))

    board = "/pkm/market" if game == "pkm" else "/market"
    under_html = (f'<div class="nz-h"><h2>Under typical now</h2><small>{A["under_n"]} live</small>'
                  f'<a href="{board}">Full board →</a></div>'
                  '<p class="nz-lead">Live asks below what the exact printing sells for, after the fees to sell it.</p>'
                  + ('<div class="nz-grid">' + "".join(under) + '</div>' if under else
                     '<p class="nz-empty">No live ask sits under the typical sold price after fees right now. '
                     'This fills as asks come in.</p>'))
    grade_head = (f'<div class="nz-h"><h2>Buy raw → grade → sell PSA 10</h2><small>{len(graded)} picks</small>'
                  f'<a href="{board}">Full board →</a></div>')
    if graded:
        grade_body = (f'<p class="nz-lead">Costs from your profile: {M(A["fixed"])} fixed per card (inbound shipping '
                      f'+ PSA fee), {A["fee_pct"] * 100:.1f}% to sell. The PSA 10 must trade at least monthly.</p>'
                      '<div class="nz-grid">' + "".join(graded) + '</div>')
    else:
        grade_body = ('<p class="nz-lead">No raw ask clears grading and selling fees with a live PSA 10 market '
                      'behind it right now.</p>')
    return ('<section class="nz-zone"><div class="mk-top"><h1>Now · ' + e(name) + '</h1>' + tabs + '</div>'
            '<p class="nz-lead">The two things worth acting on this pass. The picture is the seller\'s own where the '
            'listing has one; the chips under each card are its newest real sales.</p>'
            + under_html + grade_head + grade_body + '</section>')


def _q(d: dict) -> str:
    from urllib.parse import urlencode
    return urlencode(d)
