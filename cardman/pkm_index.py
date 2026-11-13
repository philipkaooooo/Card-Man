"""The Pokémon side of the card pages and search, in the same shape as the Yu-Gi-Oh! CardIndex, so the shared pages
(Home, Listings, Cards, a card) can render either game. Card keys are pkm-<set>-<number>."""
from __future__ import annotations

from dataclasses import dataclass

from .games import pkm_card_id
from .pkm_catalog import PkmCatalog, card_key


@dataclass
class PkmHit:
    passcode: str            # the card key, named like the Yu-Gi-Oh! field so shared code can use it
    name: str
    name_ja: str | None
    matched: str
    score: float


class PkmIndex:
    game = "pkm"

    def __init__(self, catalog: PkmCatalog):
        self.cat = catalog

    def card(self, key: str) -> dict | None:
        cid = pkm_card_id(key)
        c = self.cat.card(cid)
        if not c:
            return None
        s = self.cat.set(c["set"]["id"]) or {}
        sub = " · ".join(c.get("subtypes") or [])
        return {"game": "pkm", "passcode": card_key(cid), "card_id": cid, "name_en": c["name"], "name_ja": None,
                "card_type": c.get("supertype"), "type_line": sub, "set_name": s.get("name"), "series": s.get("series"),
                "number": c.get("number"), "printed_total": s.get("printed_total"), "release": s.get("release"),
                "hp": c.get("hp"), "types": c.get("types") or [], "rules": c.get("rules") or [], "abilities": c.get("abilities") or [],
                "attacks": c.get("attacks") or [], "weaknesses": c.get("weaknesses") or [], "resistances": c.get("resistances") or [],
                "retreat": len(c.get("retreatCost") or []), "artist": c.get("artist"), "rarity": c.get("rarity"),
                "regulation_mark": c.get("regulationMark"), "flavor": c.get("flavorText"), "evolves_from": c.get("evolvesFrom"),
                "images": c.get("images") or {}, "legalities": c.get("legalities") or {}}

    def printings(self, key: str) -> list:
        return self.cat.versions(pkm_card_id(key))

    def search(self, q: str, limit: int = 48) -> list[PkmHit]:
        return [PkmHit(h["key"], h["name"], None, f'{h["set"]} {h["number"]}/{h["printed_total"]}', h["score"])
                for h in self.cat.search(q, limit)]

    def hit_meta(self, key: str) -> dict:
        c = self.cat._cards.get(pkm_card_id(key))
        if not c:
            return {}
        s = self.cat.set(c[3]) or {}
        return {"set": s.get("name"), "number": c[4], "total": s.get("printed_total"), "rarity": c[6], "release": c[9]}

    def suggest(self, q: str, limit: int = 8) -> list[dict]:
        out = []
        for h in self.cat.search(q, limit):
            out.append({"passcode": h["key"], "name": h["name"], "name_ja": None,
                        "sub": f'{h["set"]} · {h["number"]}/{h["printed_total"]} · {h["rarity"] or ""}', "href": f'/pkm/card/{h["card_id"]}'})
        return out

    def printing_scan(self, key, printing):
        return None

    def known_scan(self, filename: str) -> bool:
        return False

    def image_url(self, key: str, size: str = "small") -> str | None:
        return self.cat.image_url(pkm_card_id(key), size)
