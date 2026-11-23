"""The Characters tab: one official picture per Pokémon, in dex order, counted from this catalog.

The per-card prices stay on the character page that already exists. This file draws the grid,
ranks it with PriceCharting money, and serves the picture. It does not price anything itself.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECIES = ROOT / "data" / "pokemon_species.json"
ICON = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{dex}.png"

_DISPLAY = {
    "mr-mime": "Mr. Mime", "mime-jr": "Mime Jr.", "mr-rime": "Mr. Rime",
    "ho-oh": "Ho-Oh", "porygon-z": "Porygon-Z",
    "jangmo-o": "Jangmo-o", "hakamo-o": "Hakamo-o", "kommo-o": "Kommo-o",
    "farfetchd": "Farfetch'd", "sirfetchd": "Sirfetch'd",
    "nidoran-f": "Nidoran♀", "nidoran-m": "Nidoran♂",
    "type-null": "Type: Null", "flabebe": "Flabébé",
}

_GEN_EDGES = (151, 251, 386, 493, 649, 721, 809, 905, 10 ** 6)
_GEN_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX")
_GEN_HUE = ("#d94f38", "#b98a1e", "#1c8f74", "#3f6fc9", "#7a5bb5",
            "#c34f8f", "#b8702a", "#56743a", "#2f8fa8")

SORTS = ("dex", "cap", "top", "move", "pace")


def _key(text: str) -> str:
    t = (text or "").lower().replace("♀", " f").replace("♂", " m").replace("é", "e").replace("è", "e")
    t = t.replace("'", "").replace("’", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class _Book:
    def __init__(self, rows: list[dict]):
        self.by_key: dict[str, int] = {}
        self.by_dex: dict[int, str] = {}
        for r in rows:
            key = _key(r["name"].replace("-", " "))
            self.by_key[key] = int(r["dex"])
            self.by_dex[self.by_key[key]] = _DISPLAY.get(r["name"], r["name"].replace("-", " ").title())

    def dex(self, species: str) -> int | None:
        words = _key(species).split()
        for n in range(len(words), 0, -1):
            for i in range(len(words) - n + 1):
                hit = self.by_key.get(" ".join(words[i:i + n]))
                if hit:
                    return hit
        return None


def book() -> _Book:
    return _Book(json.loads(SPECIES.read_text()) if SPECIES.exists() else [])


def _gen(dex: int) -> int:
    for i, edge in enumerate(_GEN_EDGES):
        if dex <= edge:
            return i + 1
    return len(_GEN_ROMAN)


def entries(cat) -> list[dict]:
    """One row per physical Pokémon: dex, name, slug, and every card id behind the tile."""
    if cat is None:
        return []
    from .character import slug, species_index
    who = book()
    merged: dict[int, tuple[set[str], set[str]]] = {}
    for name, cids in species_index(cat).items():
        dex = who.dex(name)
        if dex:
            slot = merged.setdefault(dex, (set(), set()))
            slot[1].add(name)
            slot[0].update(cids)
    out = []
    for dex, (cids, names) in sorted(merged.items()):
        label = who.by_dex.get(dex) or sorted(names)[0]
        out.append({"dex": dex, "name": label, "slug": slug(label), "cids": sorted(cids)})
    return out


def rows(cat) -> list[tuple]:
    """(slug, name, dex, card count) in dex order. Kept for callers that only need the four fields."""
    return [(e["slug"], e["name"], e["dex"], len(e["cids"])) for e in entries(cat)]


def _pkm_series(cid: str) -> str:
    """Catalog id back from a printing id: pk:EN:base1-4:holo@pkm-base1-4 -> base1-4."""
    tail = cid.rsplit("@", 1)[-1]
    return tail[4:] if tail.startswith("pkm-") else tail


MOVE_DAYS = 365
FRESH_DAYS = 45
_CHUNK = 400


def _week(points, now) -> tuple[float | None, float | None, bool]:
    """Latest price, the move since the price a year ago, and whether the newest point is current.
    A move is only claimed when the stored history reaches back a full year - no half-window labelled as 12 months."""
    pts = sorted((int(x), float(v)) for x, v in (points or []) if v is not None and float(v) > 0)
    if not pts:
        return None, None, False
    cut = (now - timedelta(days=MOVE_DAYS)).timestamp() * 1000
    base = None
    for x, v in pts:
        if x < cut:
            base = v
        else:
            break
    last = pts[-1][1]
    move = (last - base) / base * 100.0 if base and base > 0 else None
    fresh = pts[-1][0] >= (now - timedelta(days=FRESH_DAYS)).timestamp() * 1000
    return last, move, fresh


_CANS: dict[int, tuple[object, dict[str, str]]] = {}


def _cans(cat) -> dict[str, str]:
    """Card id -> the printing PriceCharting's one product per card answers for. Built once per catalog."""
    hit = _CANS.get(id(cat))
    if hit is not None and hit[0] is cat:
        return hit[1]
    from .pc import canonical
    cids = cat.by_card.keys() if getattr(cat, "by_card", None) else cat._cards.keys()
    out = {cid: pid for cid in cids for pid in [canonical(cat, cid)] if pid}
    _CANS[id(cat)] = (cat, out)   # keep cat referenced so its id cannot be reused by another object
    return out


def macro(st, cat, by_dex: dict[int, list[str]], now) -> dict[int, dict]:
    """Per character, from the merged PriceCharting history and our own sales: cap, top card, 12-mo move, pace.

    cap      - sum of the latest Ungraded PriceCharting price over every priced printing of the character.
    top      - the single most valuable of those printings; the high-to-low read of one card.
    move     - cap-weighted change from a year before the newest point to the newest point, in percent.
               Only series whose newest point is current (FRESH_DAYS) carry a move, so stale captures do not vote.
    pace     - copies of this character's cards sold per month, from our own sales ledger.
    priced   - cards with a usable series; cards is every card the catalog holds. One PC product per card.
    """
    if st is None or cat is None:
        return {}
    from . import pc
    cans = _cans(cat)
    pids = sorted({cans[c] for cids in by_dex.values() for c in cids if c in cans})
    row_of: dict[str, dict] = {}
    for i in range(0, len(pids), _CHUNK):
        row_of.update(pc.rows_for(st, pids[i:i + _CHUNK]))
    have = {r[0] for r in st.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    pace: dict[str, float] = {}
    if "price_trend" in have:
        for pid, month in st.db.execute(
                "SELECT printing_id, sales_month FROM price_trend WHERE game='pkm' "
                "AND (gkey='|' OR gkey='') AND sales_month>0"):
            pace[_pkm_series(pid)] = pace.get(_pkm_series(pid), 0.0) + float(month)
    out: dict[int, dict] = {}
    for dex, cids in by_dex.items():
        prints = sorted({cans[c] for c in cids if c in cans})
        lasts: list[float] = []
        moves: list[tuple[float, float]] = []
        newest = 0
        for pid in prints:
            row = row_of.get(pid)
            if not row:
                continue
            last, move, fresh = _week((row.get("series") or {}).get("Ungraded"), now)
            if last is None:
                continue
            lasts.append(last)
            newest = max(newest, max((int(x) for x, _v in ((row.get("series") or {}).get("Ungraded") or [])), default=0))
            if move is not None and fresh:
                moves.append((last, move))
        cap = sum(lasts)
        top = max(lasts, default=0.0)
        wsum = sum(v for v, _m in moves)
        move = sum(v * m for v, m in moves) / wsum if wsum else None
        monthly = sum(pace.get(cid, 0.0) for cid in cids)
        out[dex] = {"cap": cap, "top": top, "move": move, "pace": monthly or None,
                    "priced": len(lasts), "cards": len(prints),
                    "seen": newest / 1000.0 if newest else None}
    return out


def icon(dex: int) -> tuple[bytes, str]:
    path = ROOT / "data" / "cache" / "icons" / f"{dex}.png"
    if path.exists() and path.stat().st_size > 80:
        return path.read_bytes(), "image/png"
    import urllib.request
    try:
        req = urllib.request.Request(ICON.format(dex=dex), headers={"User-Agent": "cardman"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, "image/png"
    except Exception:
        pass
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" rx="16" fill="#1c2430"/>'
           f'<text x="48" y="56" text-anchor="middle" font-size="22" font-family="sans-serif" fill="#e8eef4">{dex}</text></svg>')
    return svg.encode(), "image/svg+xml"


LEAN = 100


def _pace(n: float) -> str:
    if n is None:
        return ""
    return f"{n:.1f}/mo" if n < 10 else f"{n:,.0f}/mo"


def view_zone(st, cat, now, cur: str, home: str, qs: dict) -> str:
    from .ui_home import Money
    from .ui import e
    from .pc import DEX_MAX
    M = Money(st, home, cur, now)
    found = [f for f in entries(cat) if f["dex"] <= DEX_MAX]
    sort = qs.get("sort", ["dex"])[0]
    sort = sort if sort in SORTS else "dex"
    stats = macro(st, cat, {f["dex"]: f["cids"] for f in found}, now)

    def conv(usd: float | None):
        if usd is None:
            return None
        try:
            if M.fx is not None:
                return float(M.fx.convert(Decimal(str(usd)), "USD", M.home, now))
        except Exception:
            pass
        return None

    def money(usd: float | None) -> str:
        if not usd:
            return ""
        v = conv(usd)
        return M(v) if v is not None else f"${usd:,.0f}"

    def rank(f: dict):
        s = stats.get(f["dex"]) or {}
        if sort == "cap":
            return (-(s.get("cap") or 0.0), f["dex"])
        if sort == "top":
            return (-(s.get("top") or 0.0), f["dex"])
        if sort == "move":
            return (-(s["move"] if s.get("move") is not None else -1e9), f["dex"])
        if sort == "pace":
            return (-(s.get("pace") or 0.0), f["dex"])
        return (f["dex"],)

    found.sort(key=rank)
    priced = [stats[f["dex"]] for f in found if stats.get(f["dex"], {}).get("priced")]
    full = [s for s in priced if s["cards"] and s["priced"] >= s["cards"]]
    partial = len(priced) - len(full)
    cap_all = sum(s["cap"] for s in priced)
    cards_all = sum(len(f["cids"]) for f in found)
    cards_tracked = len({cid for f in found for cid in f["cids"]})   # tag-team cards sit under both characters

    tiles = []
    for f in found:
        dex, s = f["dex"], stats.get(f["dex"]) or {}
        g = _gen(dex)
        meta = []
        if s.get("priced"):
            meta.append(f'<b>{money(s["cap"])}</b>')
            if s.get("move") is not None:
                mv = s["move"]
                meta.append(f'<span class="zmv {"pos" if mv >= 0 else "neg"}">{mv:+.0f}%</span>')
            if s.get("pace"):
                meta.append(f'<span>{_pace(s["pace"])}</span>')
        else:
            meta.append('<span class="znone">no PC yet</span>')
        if s.get("priced"):
            tip_bits = [f'PriceCharting: {s["priced"]} of {s.get("cards") or 0} cards priced.',
                        f'Cap {money(s.get("cap")) or "$0"}, top card {money(s.get("top")) or "$0"}.']
            if s.get("move") is not None:
                tip_bits.append(f'12-month move {s["move"]:+.0f}%.')
            if s.get("seen"):
                age = max(0, int((now.timestamp() - s["seen"]) // 86400))
                tip_bits.append(f'Latest price read {age}d ago.')
            cap_tip = " ".join(tip_bits)
        else:
            cap_tip = ('No PriceCharting row for this character yet; the sweep fills this character\'s '
                       'printings next pass.')
        tiles.append(
            f'<a class="zd" href="/pkm/character/{e(f["slug"])}" data-name="{e(f["name"].lower())}" '
            f'style="--g:{_GEN_HUE[g - 1]}" title="{e(f["name"])} #{dex} - {e(cap_tip)}">'
            f'<span class="zplate">'
            f'<span class="zno">#{dex:03d}</span><span class="zgen">Gen {_GEN_ROMAN[g - 1]}</span>'
            f'<img src="/pkm/icon/{dex}.png" alt="" width="120" height="120" loading="lazy" decoding="async">'
            f'<span class="zid"><b>{e(f["name"])}</b><em title="{len(f["cids"])} cards in the catalog">{len(f["cids"])}</em></span>'
            f'</span>'
            f'<span class="zmeta">{"".join(meta)}</span></a>')

    shown, hidden = "".join(tiles[:LEAN]), "".join(tiles[LEAN:])
    more = (f'<template id="zrest">{hidden}</template>'
            f'<div class="zmorewrap"><button id="zmore" type="button">Show all {len(found)} characters</button></div>'
            if hidden else "")

    def chip(key: str, label: str, tip: str) -> str:
        on = sort == key
        href = "/pkm/characters" + ("" if key == "dex" else f"?sort={key}")
        return (f'<a class="zchip{" on" if on else ""}" href="{href}" title="{e(tip)}"'
                + (' aria-current="true"' if on else "") + f'>{label}</a>')

    chips = (chip("dex", "Dex order", "The National Dex, Gen I first.")
             + chip("cap", "Market cap", "Every priced printing's latest PriceCharting Ungraded price, summed. High to low.")
             + chip("top", "Top card", "The single most valuable printing of the character. High to low.")
             + chip("move", "12-mo move", "Cap-weighted change from a year before the newest stored point to the newest "
                                          "point. Characters younger than a year in PriceCharting history show no move.")
             + chip("pace", "Sales pace", "Copies of this character's cards sold per month, from our own ledger."))

    tape = (f'<b>{len(full)}</b> of {len(found)} fully priced · <b>{partial}</b> partial · '
            f'<b>{cards_tracked:,}</b> cards tracked · <b>{money(cap_all)}</b> market cap'
            if priced else
            f'<b>{cards_all:,}</b> cards across {len(found)} characters. No PriceCharting row yet - '
            f'the sweep is filling every card of the first {DEX_MAX}.')

    empty = '<p class="empty">The Pokémon card list is still loading.</p>' if cat is None else ""
    return (f'<style>main.page{{max-width:min(96vw,1840px)}}</style>'
            f'<section class="zone">'
            f'<div class="zhead"><h1>Characters</h1>'
            f'<p class="sub">One tile per Pokémon, the first {DEX_MAX} by National Dex. The picture is the Pokémon, the '
            f'box is its name and card count, and the line under it is what its PriceCharting printings are worth today.</p></div>'
            f'<div class="ztools"><div class="zchips">{chips}</div>'
            f'<label class="zfindwrap"><svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
            f'<circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/>'
            f'<path d="M16.5 16.5 21 21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
            f'<input id="cfind" class="cfind" placeholder="Find a character" aria-label="Find a character" '
            f'autocomplete="off"></label></div>'
            f'<p class="ztape">{tape}</p>'
            f'<div class="zgrid">{shown}</div>{more}{empty}</section>')


CHAR_CSS = """
section.zone{padding-top:4px}
.zhead h1{font-size:clamp(28px,4vw,40px);letter-spacing:-.02em;margin:0 0 4px}
section.zone .sub{margin:0 0 14px}
.ztools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 8px}
.zchips{display:flex;gap:6px;flex-wrap:wrap}
.zchip{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:7px 14px;
font-size:13px;font-weight:650;color:var(--fg2);text-decoration:none;box-shadow:var(--shadow);
transition:border-color .15s,color .15s,box-shadow .15s}
.zchip:hover{color:var(--fg);border-color:var(--axis)}
.zchip.on{color:var(--fg);border-color:var(--axis);box-shadow:var(--shadow-hi)}
.zfindwrap{flex:1 1 220px;max-width:380px;margin-left:auto;display:flex;flex-direction:row;align-items:center;gap:8px;
border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:0 14px;box-shadow:var(--shadow);
color:var(--fg2)}
.zfindwrap:focus-within{border-color:var(--axis);box-shadow:var(--shadow-hi)}
.cfind{flex:1;border:0;background:none;outline:0;padding:10px 0;font-size:14px;color:var(--fg);min-width:0}
.ztape{margin:2px 0 20px;font-size:13px;color:var(--fg2)}
.ztape b{color:var(--fg);font-weight:700}
.zgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(142px,1fr));gap:20px 14px}
.zd{display:block;text-decoration:none;color:var(--fg)}
.zplate{position:relative;display:block;aspect-ratio:1/1.02;border-radius:18px;overflow:hidden;
background:radial-gradient(130% 96% at 50% -6%,color-mix(in oklab,var(--g) 34%,var(--surface)) 0%,
var(--surface) 66%);
border:1px solid color-mix(in oklab,var(--g) 42%,var(--line));
box-shadow:0 10px 24px color-mix(in oklab,var(--g) 14%,rgba(20,40,30,.12)),var(--shadow);
transition:transform .18s var(--ease),box-shadow .18s var(--ease)}
.zplate::before{content:"";position:absolute;inset:7px;border-radius:12px;pointer-events:none;
border:1px solid color-mix(in oklab,var(--g) 30%,transparent)}
.zd:hover .zplate{transform:translateY(-4px);box-shadow:0 16px 30px color-mix(in oklab,var(--g) 20%,rgba(20,40,30,.14)),var(--shadow-hi)}
.zno{position:absolute;top:10px;left:12px;z-index:3;font-size:11px;font-weight:750;letter-spacing:.04em;
color:color-mix(in oklab,var(--g) 72%,var(--fg))}
.zgen{position:absolute;top:10px;right:12px;z-index:3;font-size:10px;font-weight:700;letter-spacing:.05em;
text-transform:uppercase;color:color-mix(in oklab,var(--g) 58%,var(--fg2))}
.zplate img{position:absolute;left:50%;bottom:24%;transform:translateX(-50%);width:auto;height:auto;
max-width:78%;max-height:56%;z-index:2;
filter:drop-shadow(0 10px 12px color-mix(in oklab,var(--g) 22%,rgba(10,30,20,.18)));
transition:transform .18s var(--ease)}
.zd:hover .zplate img{transform:translateX(-50%) translateY(-3px) scale(1.03)}
.zid{position:absolute;left:8px;right:8px;bottom:8px;z-index:1;display:flex;align-items:center;
justify-content:space-between;gap:8px;padding:6px 8px 6px 11px;border-radius:10px;
background:color-mix(in oklab,var(--surface) 82%,var(--g) 18%);
border:1px solid color-mix(in oklab,var(--g) 34%,var(--line))}
.zid b{font-size:13.5px;font-weight:750;line-height:1.2;letter-spacing:-.01em;min-width:0;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.zid em{flex:0 0 auto;font-style:normal;font-size:11px;font-weight:750;color:var(--fg2);
background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:1px 7px;line-height:1.5}
.zmeta{display:flex;align-items:center;justify-content:center;gap:5px;flex-wrap:wrap;
font-size:12px;color:var(--fg2);padding:7px 2px 0;min-height:18px}
.zmeta b{color:var(--fg);font-weight:750}
.zmeta span+span::before{content:"·";margin-right:5px;color:var(--line-strong,var(--line))}
.zmv.pos{color:#0f9a86;font-weight:700}
.zmv.neg{color:#c4553b;font-weight:700}
.zmv.pos::before,.zmv.neg::before{color:inherit}
.znone{color:var(--fg2);opacity:.72}
.zmorewrap{grid-column:1/-1;text-align:center;margin:22px 0 4px}
.zmorewrap button{border:1px solid var(--axis);border-radius:999px;background:var(--surface);color:var(--fg);
font-weight:650;font-size:14px;padding:9px 22px;cursor:pointer;box-shadow:var(--shadow)}
.zmorewrap button:hover{box-shadow:var(--shadow-hi)}
@media (max-width:640px){.zgrid{grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:16px 10px}
.zfindwrap{margin-left:0;max-width:none;width:100%}}
"""
