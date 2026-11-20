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


def cards_of(cat, species: str) -> list[str]:
    """Card ids of this character, newest set first. Empty when the catalog has no such name."""
    want = species.strip().lower()
    if not want or cat is None:
        return []
    hit = []
    for cid, r in cat._cards.items():
        if want in {s.lower() for s in species_of(r[1])}:
            hit.append(cid)
    hit.sort(key=lambda c: (cat._cards[c][9] or "", c), reverse=True)
    return hit


def find_species(cat, slug_text: str) -> str | None:
    """The character name whose slug is this, if any card uses it."""
    want = slug_text.strip().lower()
    for _cid, r in cat._cards.items():
        for s in species_of(r[1]):
            if slug(s) == want:
                return s
    return None
