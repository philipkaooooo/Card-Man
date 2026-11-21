"""Raw card condition, normalized to one scale, and the price ratios used when a comp set is thin.

Codes: NM, EX, LP, MP, HP, DMG. Anything that is not a condition ("Used", "Pre-owned", an empty
field) maps to None: unknown, never guessed. eBay's coarse item condition is therefore None; the
listing title or a manual import has to name the condition for it to count.

The ratios are the usual market ladder relative to Near Mint. They are only applied when a printing
has fewer than MIN_SALES sales in the exact condition being priced, and every adjusted comp is
labelled in the valuation note. A comp with unknown condition is treated as the NM baseline.
"""
from __future__ import annotations

import re

CODES = ("NM", "EX", "LP", "MP", "HP", "DMG")

FACTOR = {"NM": 1.00, "EX": 0.90, "LP": 0.78, "MP": 0.62, "HP": 0.45, "DMG": 0.30}

_ALIASES = {
    "nm": "NM", "near mint": "NM", "nmmt": "NM", "mint": "NM", "m": "NM",
    "ex": "EX", "excellent": "EX", "excellent condition": "EX", "excellent plus": "EX",
    "lp": "LP", "lightly played": "LP", "light play": "LP", "lightly played condition": "LP",
    "mp": "MP", "moderately played": "MP", "played": "MP", "good": "MP", "gd": "MP",
    "hp": "HP", "heavily played": "HP", "heavy play": "HP", "poor": "HP",
    "dmg": "DMG", "damaged": "DMG", "damage": "DMG",
}
_PHRASES = (
    ("near mint", "NM"), ("near-mint", "NM"), ("lightly played", "LP"), ("light play", "LP"),
    ("moderately played", "MP"), ("heavily played", "HP"), ("heavy play", "HP"),
    ("excellent", "EX"), ("damaged", "DMG"),
    ("\u6975\u7f8e\u54c1", "NM"), ("\u5b8c\u7f8e\u54c1", "NM"), ("\u7f8e\u54c1", "EX"),
    ("\u50b7\u3042\u308a", "MP"), ("\u30ad\u30ba\u3042\u308a", "MP"), ("\u521d\u671f\u50b7", "MP"),
    ("\u767d\u6b20\u3051", "MP"), ("\u30b8\u30e3\u30f3\u30af", "HP"), ("\u6298\u308c", "HP"),
)


def normalize(value) -> str | None:
    """One code for a condition string, or None when it does not name one."""
    if value is None:
        return None
    s = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]+", " ", str(value).casefold()).strip()
    if not s:
        return None
    if s in _ALIASES:
        return _ALIASES[s]
    for tok in s.split():
        if tok.upper() in CODES:
            return tok.upper()
    for phrase, code in _PHRASES:
        if phrase in s:
            return code
    return None


def factor(code: str | None) -> float:
    """Price ratio to Near Mint. Unknown is the NM baseline, never a discount."""
    return FACTOR.get(code or "", 1.0)


def adjust(amount: float, comp: str | None, target: str | None) -> float:
    """`amount` observed on a comp in `comp` condition, restated as a copy in `target` condition."""
    if not target or amount <= 0:
        return amount
    return amount * factor(target) / factor(comp)


def label(code: str | None) -> str:
    return code if code else "condition not known"
