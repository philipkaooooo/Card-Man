"""A character is every card of one Pokémon. Each card keeps its own price.

Charizard ex and Charizard VSTAR are both Charizard. They are never one price. The only thing
that groups them is the character's market: which prints exist, how each one moved, and how they
sit next to each other. A listing is never priced from a different print.
"""
from __future__ import annotations

import re

_LEAD = re.compile(r"^(mega|radiant|shining|light|dark|m)$", re.I)
_TAIL = re.compile(r"^(ex|gx|v|vmax|vstar|v-union|break|prime|legend|star|lv\.x)$", re.I)


def species_of(name: str) -> list[str]:
    """The Pokémon in a card name. A tag-team card is both. A form (ex, VSTAR, Mega) is not a different character."""
    text = name.replace("-EX", " EX").replace("-GX", " GX").replace("δ", " ")
    parts = [p for p in text.split() if p]
    while parts and _LEAD.match(parts[0]):
        parts.pop(0)
    while parts and _TAIL.match(parts[-1]):
        parts.pop()
    text = " ".join(parts).strip() or name.strip()
    return [p.strip() for p in re.split(r"\s+&\s+", text) if p.strip()] or [name.strip()]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


_INDEX: dict[int, tuple[object, dict]] = {}


def species_index(cat) -> dict[str, list[str]]:
    """Character name -> its card ids, newest set first. Built once per catalog instance."""
    if cat is None:
        return {}
    hit = _INDEX.get(id(cat))
    if hit is not None and hit[0] is cat:
        return hit[1]
    idx: dict[str, list[str]] = {}
    for cid, r in cat._cards.items():
        for s in species_of(r[1]):
            idx.setdefault(s, []).append(cid)
    for v in idx.values():
        v.sort(key=lambda c: (cat._cards[c][9] or "", c), reverse=True)
    _INDEX[id(cat)] = (cat, idx)   # keep cat referenced so its id cannot be reused by another object
    return idx


def cards_of(cat, species: str) -> list[str]:
    """Card ids of this character, newest set first. Empty when the catalog has no such name."""
    want = species.strip().lower()
    for name, cids in species_index(cat).items():
        if name.lower() == want:
            return cids
    return []


def find_species(cat, slug_text: str) -> str | None:
    """The character name whose slug is this, if any card uses it."""
    want = slug_text.strip().lower()
    for s in species_index(cat):
        if slug(s) == want:
            return s
    return None


_MARKETS: dict[int, tuple[object, list]] = {}


def market_rows(cat) -> list[tuple[int, int, str, str]]:
    """Every character with market evidence, best first: (cards with a guide, cards owned, name, representative card id).
    The representative is the newest print whose picture is already cached, so the grid opens on real art."""
    if cat is None:
        return []
    hit = _MARKETS.get(id(cat))
    if hit is not None and hit[0] is cat:
        return hit[1]
    from .cardinfo import IMAGES
    rows = []
    for name, cids in species_index(cat).items():
        priceable = sum(1 for c in cids if any(cat.guide(v.id) for v in cat.versions(c)))
        if not priceable:
            continue
        rep = next((c for c in cids if any((IMAGES / "pkm" / s / f"{c}.{x}").exists()
                                           for s, x in (("small", "png"), ("small", "jpg"), ("large", "png"), ("large", "jpg")))),
                   cids[0])
        rows.append((priceable, len(cids), name, rep))
    rows.sort(reverse=True)
    _MARKETS[id(cat)] = (cat, rows)   # keep cat referenced so its id cannot be reused by another object
    return rows
