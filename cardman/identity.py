"""Exact-print resolver with abstention (CLAUDE.md section 1).

The resolver only narrows a catalog of known printings. It never invents a
printing, and it returns RESOLVED only when every dimension that separates the
remaining candidates is backed by evidence that is not just seller text.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from .models import CardFamily, GameFormat, IdentityStatus, Printing, Region

RESOLVER_VERSION = "identity-0.1.0"

# Which evidence separates candidates on each dimension.
DIMENSION_EVIDENCE = {
    "edition": "clear photo of the edition stamp area (below artwork, left of the card text box)",
    "rarity": "angled photo showing foil on name/artwork/set code (seller rarity text is not proof)",
    "set_code": "close-up of the printed set code",
    "region": "close-up of set code and card back / language of the card text",
    "product": "product/promo reference or set code close-up",
    "artwork": "front photo of the full artwork",
}

_SET_CODE_RE = re.compile(r"\b(RD/)?([A-Z0-9]{2,5})-(EN|E|JP|AE|KR|DE|FR|IT|SP|PT)?([0-9]{2,3}|[A-Z][0-9]{2})\b")
_CERT_RE = re.compile(r"^\d{7,10}$")


@dataclass(frozen=True)
class ListingSignals:
    """What a listing / photo set actually shows. Seller-entered text is kept apart."""
    title: str
    printed_set_code: str | None = None       # read from the card image (verified), or None
    seller_set_code: str | None = None        # what the seller typed
    seller_rarity: str | None = None
    verified_rarity: str | None = None        # confirmed from angled imagery
    verified_edition: str | None = None       # confirmed from edition-stamp photo
    seller_edition: str | None = None
    card_language: str | None = None          # from visible card text, e.g. "ja", "en"
    slab_cert: str | None = None
    product_hint: str | None = None
    is_multi_card: bool = False
    photos: tuple[str, ...] = ()


@dataclass
class IdentityResult:
    status: IdentityStatus
    candidates: list[Printing]
    evidence: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    evidence_needed: list[str] = field(default_factory=list)
    resolver_version: str = RESOLVER_VERSION

    @property
    def printing(self) -> Printing | None:
        return self.candidates[0] if self.status == IdentityStatus.RESOLVED else None


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_set_code(raw: str | None) -> dict | None:
    if not raw:
        return None
    raw = unicodedata.normalize("NFKC", raw).upper().strip()
    if _CERT_RE.match(raw):
        return {"kind": "cert_number", "raw": raw}
    m = _SET_CODE_RE.search(raw)
    if not m:
        return None
    rush, prefix, lang, num = m.groups()
    lang = lang or ""
    region = {
        "EN": Region.EN_TCG, "E": Region.EN_TCG,
        "JP": Region.JP_OCG,
        "AE": Region.ASIAN_EN_OCG_QUARANTINE,
    }.get(lang)
    return {
        "kind": "set_code",
        "raw": raw,
        "code": f"{rush or ''}{prefix}-{lang}{num}",
        "prefix": prefix,
        "lang": lang,
        "region": region,
        "game_format": GameFormat.RUSH_DUEL if rush else GameFormat.DUEL_MONSTERS,
    }


class Catalog:
    def __init__(self, families: list[CardFamily], printings: list[Printing]):
        self.families = {f.id: f for f in families}
        self.printings = printings

    def family_matches(self, title: str, cutoff: float = 0.85) -> tuple[set[str], list[str]]:
        """Names/aliases contained in the title, plus fuzzy matches for seller typos."""
        t = normalize_name(title)
        exact, notes = set(), []
        for f in self.families.values():
            for n in (*f.names.values(), *f.aliases):
                if normalize_name(n) and normalize_name(n) in t:
                    exact.add(f.id)
        if exact:
            return exact, notes
        words = t.split()
        for f in self.families.values():
            for n in (*f.names.values(), *f.aliases):
                nn = normalize_name(n)
                k = len(nn.split())
                for i in range(max(1, len(words) - k + 1)):
                    window = " ".join(words[i:i + k])
                    r = difflib.SequenceMatcher(None, nn, window).ratio()
                    if r >= cutoff:
                        exact.add(f.id)
                        notes.append(f"fuzzy name match '{window}' ~ '{n}' ({r:.2f}); treat as seller typo, needs image confirmation")
        return exact, notes


def resolve(signals: ListingSignals, catalog: Catalog) -> IdentityResult:
    ev: list[str] = []
    block: list[str] = []

    if signals.is_multi_card:
        return IdentityResult(
            IdentityStatus.CANDIDATES, [], ["multi-card listing"],
            ["multi-card photo: each copy must be identified separately"],
            ["one clear front+back photo per copy, or per-card list from seller"],
        )

    fam_ids, notes = catalog.family_matches(signals.title)
    ev += notes
    if not fam_ids:
        return IdentityResult(IdentityStatus.UNRESOLVED, [], ev, ["no card family matched title"],
                              ["printed card name or set code close-up"])

    cands = [p for p in catalog.printings if p.family_id in fam_ids]

    # Set code: printed (from image) is evidence; seller-typed is a hint only.
    printed = parse_set_code(signals.printed_set_code)
    seller = parse_set_code(signals.seller_set_code)
    if seller and seller["kind"] == "cert_number":
        block.append(f"seller 'set code' {seller['raw']} looks like a grading cert number; ignored as set code")
        seller = None
    if signals.slab_cert:
        ev.append("slab cert present: certificate lookup is not proof of slab or card authenticity")

    code = printed or seller
    if code and code["kind"] == "set_code":
        if code["game_format"] == GameFormat.RUSH_DUEL:
            return IdentityResult(IdentityStatus.QUARANTINED, [], ev + [f"Rush Duel code {code['code']}"],
                                  ["Rush Duel is a separate game format; not a Duel Monsters comparable"], [])
        if code["region"] == Region.ASIAN_EN_OCG_QUARANTINE:
            q = [p for p in cands if p.region == Region.ASIAN_EN_OCG_QUARANTINE]
            return IdentityResult(IdentityStatus.QUARANTINED, q, ev + [f"Asian-English OCG code {code['code']}"],
                                  ["Asian-English OCG is not English TCG; quarantined, not traded yet"], [])
        if code["region"] is None:
            block.append(f"set code language segment '{code['lang']}' is out of scope (JP/EN only)")
            return IdentityResult(IdentityStatus.UNRESOLVED, [], ev, block, [])
        matched = [p for p in cands if p.set_code and p.set_code.upper() == code["code"]]
        if not matched:
            region_only = [p for p in cands if p.region == code["region"]]
            block.append(f"set code {code['code']} not in catalog for this card")
            return IdentityResult(IdentityStatus.UNRESOLVED, region_only, ev, block,
                                  [DIMENSION_EVIDENCE["set_code"]])
        cands = matched
        ev.append(f"set code {code['code']} ({'printed' if printed else 'seller-entered'})")
        if printed and seller and seller["kind"] == "set_code" and seller["code"] != printed["code"]:
            ev.append(f"seller wrote {seller['code']} but card shows {printed['code']}; using printed code")

    # Language visible on the card constrains region; never map JP <-> EN silently.
    if signals.card_language:
        want = {"ja": {Region.JP_OCG}, "en": {Region.EN_TCG, Region.ASIAN_EN_OCG_QUARANTINE}}.get(signals.card_language)
        if want:
            before = cands
            cands = [p for p in cands if p.region in want]
            if before and not cands:
                return IdentityResult(IdentityStatus.CONFLICT, before, ev,
                                      [f"card language {signals.card_language} contradicts set code region"], [])
            ev.append(f"card language {signals.card_language}")

    # Verified edition and rarity narrow; seller text does not.
    if signals.verified_edition:
        cands = [p for p in cands if (p.edition or "").lower() == signals.verified_edition.lower()]
        ev.append(f"edition {signals.verified_edition} (verified from image)")
    if signals.verified_rarity:
        cands = [p for p in cands if p.rarity.lower() == signals.verified_rarity.lower()]
        ev.append(f"rarity {signals.verified_rarity} (verified from angled image)")

    if not cands:
        return IdentityResult(IdentityStatus.CONFLICT, [], ev, block + ["verified evidence matches no known printing"], [])

    # Seller claims that disagree with every remaining candidate are flagged.
    if signals.seller_rarity and not any(p.rarity.lower() == signals.seller_rarity.lower() for p in cands):
        ev.append(f"seller rarity '{signals.seller_rarity}' matches none of the candidates")
    if signals.seller_edition and not signals.verified_edition:
        ev.append(f"seller says edition '{signals.seller_edition}' (unverified)")

    if len(cands) == 1:
        p = cands[0]
        if notes:
            # A typo-matched name is fine for candidates but needs image confirmation.
            return IdentityResult(IdentityStatus.CANDIDATES, cands, ev, block + ["name matched only fuzzily"],
                                  ["front photo with readable card name"])
        if not (printed or p.set_code is None):
            return IdentityResult(IdentityStatus.CANDIDATES, cands, ev,
                                  block + ["set code only seller-entered"], [DIMENSION_EVIDENCE["set_code"]])
        return IdentityResult(IdentityStatus.RESOLVED, cands, ev, block, [])

    # Abstain: say exactly which evidence separates the candidates.
    needed = []
    for dim, attr in (("region", "region"), ("set_code", "set_code"), ("edition", "edition"),
                      ("rarity", "rarity"), ("artwork", "artwork_id"), ("product", "product_id")):
        if len({getattr(p, attr) for p in cands}) > 1:
            needed.append(DIMENSION_EVIDENCE[dim])
    return IdentityResult(IdentityStatus.CANDIDATES, cands, ev,
                          block + [f"{len(cands)} printings remain"], list(dict.fromkeys(needed)))
