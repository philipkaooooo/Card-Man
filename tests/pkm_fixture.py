"""A small Pokémon catalog in the Pokémon TCG API's own shape (made-up prices, real card numbers), for tests."""
import gzip
import json
from pathlib import Path

SETS = [
    {"id": "base1", "name": "Base", "series": "Base", "printedTotal": 102, "total": 102, "ptcgoCode": "BS", "releaseDate": "1999/01/09"},
    {"id": "swsh7", "name": "Evolving Skies", "series": "Sword & Shield", "printedTotal": 203, "total": 237, "ptcgoCode": "EVS",
     "releaseDate": "2021/08/27"},
    {"id": "sma", "name": "Hidden Fates Shiny Vault", "series": "Sun & Moon", "printedTotal": 94, "total": 94, "releaseDate": "2019/08/23"},
    {"id": "swshp", "name": "SWSH Black Star Promos", "series": "Promo", "printedTotal": 307, "total": 307, "ptcgoCode": "PR-SW",
     "releaseDate": "2019/11/15"},
    {"id": "sv3pt5", "name": "151", "series": "Scarlet & Violet", "printedTotal": 165, "total": 207, "ptcgoCode": "MEW", "releaseDate": "2023/09/22"},
    {"id": "sv4pt5", "name": "Paldean Fates", "series": "Scarlet & Violet", "printedTotal": 91, "total": 245, "ptcgoCode": "PAF",
     "releaseDate": "2024/01/26"},
]


def _card(sid, num, name, rarity, prices, supertype="Pokémon", **kw):
    s = next(x for x in SETS if x["id"] == sid)
    return {"id": f"{sid}-{num}", "name": name, "supertype": supertype, "subtypes": kw.pop("subtypes", ["Basic"]), "hp": kw.pop("hp", "100"),
            "types": kw.pop("types", ["Fire"]), "number": num, "rarity": rarity, "artist": kw.pop("artist", "Someone"),
            "set": {"id": sid, "name": s["name"], "series": s["series"], "printedTotal": s["printedTotal"], "releaseDate": s["releaseDate"]},
            "images": {"small": f"https://images.pokemontcg.io/{sid}/{num}.png", "large": f"https://images.pokemontcg.io/{sid}/{num}_hires.png"},
            "tcgplayer": {"updatedAt": "2026/11/12", "prices": {k: {"market": v} for k, v in prices.items()}}, **kw}


CARDS = [
    _card("base1", "4", "Charizard", "Rare Holo", {"1stEditionHolofoil": 9000.0, "unlimitedHolofoil": 450.0}, hp="120",
          attacks=[{"name": "Fire Spin", "cost": ["Fire"] * 4, "damage": "100", "text": "Discard 2 Energy cards."}],
          weaknesses=[{"type": "Water", "value": "×2"}], retreatCost=["Colorless"] * 3, artist="Mitsuhiro Arita"),
    _card("base1", "58", "Pikachu", "Common", {"1stEdition": 60.0, "unlimited": 6.0}, types=["Lightning"], hp="40"),
    _card("swsh7", "215", "Umbreon VMAX", "Rare Secret", {"holofoil": 1400.0}, types=["Darkness"], subtypes=["VMAX"], hp="310"),
    _card("swsh7", "95", "Umbreon VMAX", "Rare Holo VMAX", {"holofoil": 25.0}, types=["Darkness"], subtypes=["VMAX"], hp="310"),
    _card("swsh7", "218", "Rayquaza VMAX", "Rare Secret", {"holofoil": 650.0}, types=["Dragon"], subtypes=["VMAX"]),
    _card("sma", "SV49", "Charizard-GX", "Rare Shiny GX", {"holofoil": 120.0}, subtypes=["GX"]),
    _card("swshp", "SWSH050", "Pikachu V", "Promo", {"holofoil": 4.0}, types=["Lightning"], subtypes=["V"]),
    _card("sv3pt5", "199", "Charizard ex", "Special Illustration Rare", {"holofoil": 320.0}, subtypes=["Stage 2", "ex"], hp="330",
          regulationMark="G"),
    _card("sv3pt5", "6", "Charizard ex", "Double Rare", {"holofoil": 9.0}, subtypes=["Stage 2", "ex"], hp="330"),
    _card("sv3pt5", "25", "Pikachu", "Common", {"normal": 0.3, "reverseHolofoil": 1.5}, types=["Lightning"], hp="60"),
    _card("sv4pt5", "234", "Charizard ex", "Special Illustration Rare", {"holofoil": 110.0}, subtypes=["Stage 2", "ex"]),
    _card("sv3pt5", "166", "Bulbasaur", "Illustration Rare", {"holofoil": 18.0}, types=["Grass"]),
    _card("sv3pt5", "190", "Professor's Research", "Ultra Rare", {"holofoil": 3.0}, supertype="Trainer", subtypes=["Supporter"]),
]


def write_raw(raw: Path) -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "sets.json.gz").write_bytes(gzip.compress(json.dumps({"data": SETS}).encode()))
    by: dict[str, list] = {}
    for c in CARDS:
        by.setdefault(c["set"]["id"], []).append(c)
    for sid, cs in by.items():
        (raw / f"set-{sid}.json.gz").write_bytes(gzip.compress(json.dumps({"data": cs}).encode()))
    return raw


def catalog(tmp: Path):
    from cardman.pkm_catalog import PkmCatalog, build
    raw = write_raw(tmp / "pkm_raw")
    from cardman.pkm_catalog import RULES
    build(raw, tmp / "pkm.sqlite", rules=json.loads(RULES.read_text()))      # the real rule file
    return PkmCatalog(tmp / "pkm.sqlite")
