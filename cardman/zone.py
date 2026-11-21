"""The Characters tab: one official picture per Pokémon, in dex order, counted from this catalog.

The per-card prices stay on the character page that already exists. This file only draws the grid
and serves the picture. It does not price anything.
"""
from __future__ import annotations

import json
import re
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


def rows(cat) -> list[tuple]:
    """(slug, name, dex, card count) in dex order, from the catalog already loaded. No second card list."""
    if cat is None:
        return []
    from .character import slug, species_index
    who = book()
    # One tile per dex number, not per spelling: "Charizard" and "Charizard & Braixen" are the same Pokémon.
    merged: dict[int, set[str]] = {}
    for name, cids in species_index(cat).items():
        dex = who.dex(name)
        if dex:
            merged.setdefault(dex, set()).update(cids)
    return [(slug(who.by_dex.get(dex, str(dex))), who.by_dex.get(dex, str(dex)), dex, len(cids))
            for dex, cids in sorted(merged.items())]


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


def view_zone(cat) -> str:
    from .ui import e
    found = rows(cat)
    tiles = []
    for slug, name, dex, n in found:
        tiles.append(
            f'<a class="zchar" href="/pkm/character/{e(slug)}" data-name="{e(name.lower())}">'
            f'<img src="/pkm/icon/{dex}.png" alt="" width="104" height="104" loading="lazy" decoding="async">'
            f'<span class="znm"><b>{e(name)}</b><small>{n} card{"s" if n != 1 else ""}</small></span></a>')
    script = ("<script>(function(){var q=document.getElementById('cfind');if(!q)return;"
              "q.addEventListener('input',function(){var s=q.value.toLowerCase();"
              "document.querySelectorAll('.zchar').forEach(function(a){"
              "a.hidden=s && a.getAttribute('data-name').indexOf(s)<0;});});})();</script>")
    empty = '<p class="empty">The Pokémon card list is still loading.</p>' if cat is None else ""
    return (f'<style>main.page{{max-width:min(96vw,1840px)}}</style>'
            f'<section class="zone"><h1>Characters</h1>'
            f'<p class="sub">{len(found)} Pokémon with a card on this site. The picture is that Pokémon. '
            f'The number is how many different cards it has.</p>'
            f'<input id="cfind" class="cfind" placeholder="Find a character" autocomplete="off">'
            f'<div class="zgrid">{"".join(tiles)}</div>{empty}</section>{script}')


CHAR_CSS = """
section.zone{padding-top:4px}section.zone h1{font-size:clamp(28px,4vw,40px);letter-spacing:-.02em;margin:0 0 6px}
.cfind{width:min(420px,100%);margin:0 0 18px;border-radius:999px;padding:10px 16px}
.zgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:20px 10px}
.zchar{display:flex;flex-direction:column;align-items:center;gap:5px;text-align:center;text-decoration:none;color:var(--fg);
background:none;border:0;border-radius:14px;padding:4px 0 0;box-shadow:none}
.zchar img{width:104px;height:104px;object-fit:contain;filter:drop-shadow(0 10px 14px rgba(20,60,40,.13));
transition:transform .16s var(--ease)}
.zchar:hover img{transform:translateY(-4px)}
.znm{display:flex;flex-direction:column;align-items:center;max-width:100%;background:var(--surface);
border:1px solid var(--line);border-radius:999px;padding:3px 12px 4px;box-shadow:var(--shadow)}
.znm b{font-size:12.5px;font-weight:650;line-height:1.25;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.znm small{color:var(--muted);font-size:11.5px;line-height:1.2}
"""
