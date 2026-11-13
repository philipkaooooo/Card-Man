"""The two games CardMan covers, and how to tell them apart from any id. Nothing is ever pooled across games.

  ygo  Yu-Gi-Oh! (JP OCG + EN TCG)   printing ids  p:...@<passcode>          card key  89631139
  pkm  Pokémon TCG (English)          printing ids  pk:EN:...@pkm-<card id>   card key  pkm-sv3pt5-199
"""
from __future__ import annotations

import re

GAMES = ("ygo", "pkm")
NAME = {"ygo": "Yu-Gi-Oh!", "pkm": "Pokémon"}
PREFIX = {"ygo": "", "pkm": "/pkm"}            # URL prefix of every page of that game
_KEY = re.compile(r"@([^@|]+)")


def card_key(printing_id: str | None) -> str | None:
    """The card a printing belongs to: a passcode (Yu-Gi-Oh!) or pkm-<set>-<number> (Pokémon)."""
    m = _KEY.search(printing_id or "")
    return m.group(1) if m else None


def game_of(printing_id_or_key: str | None) -> str:
    s = printing_id_or_key or ""
    return "pkm" if s.startswith("pk:") or s.startswith("pkm-") or "@pkm-" in s else "ygo"


def pkm_card_id(key: str) -> str:
    return key[4:] if key.startswith("pkm-") else key


def card_href(key: str | None) -> str:
    """The card page address: /card/<passcode> for Yu-Gi-Oh!, /pkm/card/<set-number> for Pokémon."""
    k = key or ""
    return f"/pkm/card/{pkm_card_id(k)}" if k.startswith("pkm-") else f"/card/{k}"


_PAGE_LINKS = ('href="/opportunities', 'href="/search', 'action="/search"', 'href="/"', 'href="/?', 'href="/api/suggest',
               'data-suggest-url="/api/suggest')


def scope_links(html: str, game: str) -> str:
    """Pokémon pages keep you in Pokémon: the game's own pages get the /pkm prefix. Shared pages (Add sales,
    Settings, a listing, a card) are left alone; card links already carry their game (see card_href)."""
    if game != "pkm":
        return html
    for a in _PAGE_LINKS:
        attr, _, path = a.partition('="')
        html = html.replace(a, f'{attr}="/pkm{path}' if path != "/" else f'{attr}="/pkm/')
    return html.replace('href="/pkm/"', 'href="/pkm/"')


_PKM_WORDS = re.compile(r"pok[eé]mon|ポケモン|ポケカ|\bptcg\b|\bpkmn\b", re.I)
_YGO_WORDS = re.compile(r"yu-?gi-?oh|遊戯王|\bygo\b|\bocg\b|konami", re.I)
_YGO_CODE = re.compile(r"\b[A-Z0-9]{2,5}-(?:EN|JP|E|J|DE|FR|IT|SP|PT|KR|AE)?\d{2,3}\b")
_PKM_NUMBER = re.compile(r"(?<![\w/])\d{1,3}\s*/\s*\d{2,3}(?![\w/])")


def guess_game(title: str, pkm_catalog=None) -> str:
    """Which game a listing from an imported page is. Words the seller wrote win; then the shape of the number
    (LOB-EN001 vs 199/165); then whether a Pokémon card name is in the title. Yu-Gi-Oh! when nothing says otherwise."""
    t = title or ""
    p, y = bool(_PKM_WORDS.search(t)), bool(_YGO_WORDS.search(t))
    if p != y:
        return "pkm" if p else "ygo"
    if _YGO_CODE.search(t):
        return "ygo"
    if _PKM_NUMBER.search(t) and pkm_catalog is not None:
        from .pkm_intel import _names_in
        from .pkm_catalog import norm
        if _names_in(norm(t), pkm_catalog):
            return "pkm"
    return "ygo"
