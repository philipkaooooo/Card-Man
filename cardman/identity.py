"""Exact-print resolver with abstention (CLAUDE.md section 1).

The resolver only narrows a catalog of known printings. It never invents a
printing, and it returns RESOLVED only when every dimension that separates the
remaining candidates is backed by evidence that is not just seller text.
"""
from __future__ import annotations

import difflib
from dataclasses import replace
import re
import unicodedata
from dataclasses import dataclass, field

from .models import CardFamily, GameFormat, IdentityStatus, Printing, Region

RESOLVER_VERSION = "identity-0.3.0"

# Which evidence separates candidates on each dimension.
DIMENSION_EVIDENCE = {
    "edition": "clear photo of the edition stamp area (below artwork, left of the card text box)",
    "rarity": "angled photo showing foil on name/artwork/set code (seller rarity text is not proof)",
    "set_code": "close-up of the printed set code",
    "region": "close-up of set code and card back / language of the card text",
    "product": "product/promo reference or set code close-up",
    "artwork": "front photo of the full artwork",
}

_SET_CODE_RE = re.compile(r"(?<![A-Z0-9/])(RD/)?([A-Z0-9]{2,5})-([A-Z0-9]{2,7})(?![A-Z0-9])")
# Language segments, longest first. Codes with no segment (LB-01, SKE-001, JMP-001) exist in
# both JP and EN vintage products, so their region must come from the catalog and the card itself.
_LANG_SEGMENTS = ("EN", "JP", "AE", "KR", "KO", "DE", "FR", "IT", "SP", "PT", "TC", "SC", "E", "A", "G", "F", "I", "S", "P", "K")
_LANG_REGION = {"EN": Region.EN_TCG, "E": Region.EN_TCG, "A": Region.EN_TCG, "JP": Region.JP_OCG,
                "AE": Region.ASIAN_EN_OCG_QUARANTINE}
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
    product_hint: str | None = None           # seller text; never narrows
    verified_product: str | None = None       # confirmed from packaging, promo insert, or set list
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
    # First code-shaped token whose suffix has a digit ("BLUE-EYES" is a name, "LOB-EN001" a code).
    m = next((m for m in _SET_CODE_RE.finditer(raw) if any(ch.isdigit() for ch in m.group(3))), None)
    if not m:
        return None
    rush, prefix, suffix = m.groups()
    lang = ""
    if not suffix.isdigit():
        for seg in _LANG_SEGMENTS:
            rest = suffix[len(seg):]
            if suffix.startswith(seg) and rest and rest[-1].isdigit():
                lang = seg
                break
    return {
        "kind": "set_code",
        "raw": raw,
        "code": f"{rush or ''}{prefix}-{suffix}",
        "prefix": prefix,
        "lang": lang,
        # None when the code has no language segment: look the code up instead of guessing.
        "region": _LANG_REGION.get(lang),
        "game_format": GameFormat.RUSH_DUEL if rush else GameFormat.DUEL_MONSTERS,
    }


def _strip_ruby(s: str) -> tuple[str, str | None]:
    """'<ruby>青眼の白龍<rt>ブルーアイズ…</rt></ruby>' -> ('青眼の白龍', 'ブルーアイズ…')."""
    if "<ruby>" not in s:
        return s, None
    base = re.sub(r"<rt>.*?</rt>", "", s).replace("<ruby>", "").replace("</ruby>", "")
    reading = re.sub(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", r"\2", s)
    return base, reading


class Catalog:
    def __init__(self, families: list[CardFamily], printings: list[Printing]):
        self.families = {f.id: f for f in families}
        self.printings = printings
        self.by_family: dict[str, list[Printing]] = {}
        self.by_code: dict[str, list[Printing]] = {}
        for p in printings:
            self.by_family.setdefault(p.family_id, []).append(p)
            if p.set_code:
                self.by_code.setdefault(p.set_code.upper(), []).append(p)
        # normalized name -> family ids; Japanese ruby markup reduced to base text, reading kept as alias
        self.name_index: dict[str, set[str]] = {}
        for f in families:
            for n in (*f.names.values(), *f.aliases):
                for variant in _strip_ruby(n):
                    nn = normalize_name(variant) if variant else ""
                    if nn:
                        self.name_index.setdefault(nn, set()).add(f.id)
        self._by_len: dict[int, list[str]] = {}
        for nn in self.name_index:
            self._by_len.setdefault(len(nn.split()), []).append(nn)

    def family_matches(self, title: str, cutoff: float = 0.85) -> tuple[set[str], list[str]]:
        """Names contained in the title (longest wins), plus fuzzy matches for seller typos."""
        t = normalize_name(title)
        spaced = f" {t} "
        hits = []
        for nn, fams in self.name_index.items():
            # Latin names must match on word boundaries; CJK titles have no spaces.
            if (f" {nn} " in spaced) if nn.isascii() else (nn in t):
                hits.append(nn)
        # "Dark Magician" inside "Dark Magician Girl" is not a second card.
        hits = [h for h in hits if not any(h != o and h in o for o in hits)]
        exact = set().union(*(self.name_index[h] for h in hits)) if hits else set()
        notes: list[str] = []
        if exact:
            return exact, notes
        words = t.split()
        scored: list[tuple[float, str, str]] = []
        for k, names in self._by_len.items():
            if k < 2 and words and words[0].isascii():
                continue  # one-word fuzzy hits ("dragon" ~ "dragong") are noise
            for i in range(max(1, len(words) - k + 1)):
                window = " ".join(words[i:i + k])
                for nn in difflib.get_close_matches(window, names, n=3, cutoff=cutoff):
                    scored.append((difflib.SequenceMatcher(None, nn, window).ratio(), window, nn))
        if scored:
            best = max(r for r, _, _ in scored)
            for r, window, nn in sorted(set(scored), reverse=True):
                if r >= best - 0.02:
                    exact |= self.name_index[nn]
                    notes.append(f"fuzzy name match '{window}' ~ '{nn}' ({r:.2f}); treat as seller typo, "
                                 "needs image confirmation")
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

    cands = [p for fid in sorted(fam_ids) for p in catalog.by_family.get(fid, [])]

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
        if code["region"] is None and code["lang"]:
            block.append(f"set code language segment '{code['lang']}' is out of scope (JP/EN only)")
            return IdentityResult(IdentityStatus.UNRESOLVED, [], ev, block, [])
        matched = [p for p in cands if p.set_code and p.set_code.upper() == code["code"]]
        if not matched:
            region_only = [p for p in cands if code["region"] is None or p.region == code["region"]]
            block.append(f"set code {code['code']} not in catalog for this card")
            return IdentityResult(IdentityStatus.UNRESOLVED, region_only, ev, block,
                                  [DIMENSION_EVIDENCE["set_code"]])
        if code["region"] is not None and any(p.region != code["region"] for p in matched):
            block.append(f"catalog lists {code['code']} outside its language segment's region")
            return IdentityResult(IdentityStatus.CONFLICT, matched, ev, block, [])
        cands = matched
        ev.append(f"set code {code['code']} ({'printed' if printed else 'seller-entered'})")
        if code["region"] is None:
            ev.append(f"{code['code']} has no language segment; region taken from catalog and card evidence")
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

    # Verified edition, rarity and product narrow; seller text does not.
    if signals.verified_edition:
        # A catalog printing with edition=None means the identity source does not document editions;
        # keep it as a candidate but it cannot be confirmed (see the edition gate below).
        cands = [p for p in cands if p.edition is None or p.edition.lower() == signals.verified_edition.lower()]
        ev.append(f"edition {signals.verified_edition} (verified from image)")
    if signals.verified_rarity:
        cands = [p for p in cands if p.rarity.lower() == signals.verified_rarity.lower()]
        ev.append(f"rarity {signals.verified_rarity} (verified from angled image)")
    if signals.verified_product:
        want = normalize_name(signals.verified_product)
        cands = [p for p in cands if normalize_name(p.product_name or p.product_id) == want]
        ev.append(f"product {signals.verified_product} (verified from packaging/promo reference)")

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
        if p.identity_status != "documented":
            return IdentityResult(IdentityStatus.CANDIDATES, cands, ev,
                                  block + [f"catalog printing is {p.identity_status}: {'; '.join(p.variant_evidence)}"],
                                  ["identity source record with complete rarity/product data"])
        if p.region == Region.EN_TCG and p.edition is None and signals.verified_edition:
            # No identity source we can reach lists editions (checked 2026-11-05: YAML Yugi, YGOResources,
            # YGOPRODeck). The stamp photo is direct physical evidence, so the edition becomes part of
            # the printing key; comps must match this derived id, never the edition-less one.
            ed = signals.verified_edition
            derived = replace(p, id=f"{p.id}#ed={normalize_name(ed).replace(' ', '-')}", edition=ed,
                              variant_evidence=p.variant_evidence + (
                                  f"edition '{ed}' from verified stamp photo; identity sources list no editions",))
            return IdentityResult(IdentityStatus.RESOLVED, [derived], ev + ["edition from stamp photo"], block, [])
        if p.region == Region.EN_TCG and p.edition is None:
            # English prints differ by 1st / Unlimited / Limited; the loaded source does not say which exist.
            return IdentityResult(IdentityStatus.CANDIDATES, cands, ev,
                                  block + ["edition not documented by identity source; 1st/Unlimited/Limited "
                                           "cannot be confirmed"],
                                  [DIMENSION_EVIDENCE["edition"]])
        return IdentityResult(IdentityStatus.RESOLVED, cands, ev, block, [])

    # Abstain: say exactly which evidence separates the candidates.
    needed = []
    for dim, key in (("region", lambda p: p.region), ("set_code", lambda p: p.set_code),
                     ("edition", lambda p: p.edition), ("rarity", lambda p: p.rarity),
                     ("artwork", lambda p: p.artwork_id), ("product", lambda p: p.product_name or p.product_id)):
        if len({key(p) for p in cands}) > 1:
            needed.append(DIMENSION_EVIDENCE[dim])
    return IdentityResult(IdentityStatus.CANDIDATES, cands, ev,
                          block + [f"{len(cands)} printings remain"], list(dict.fromkeys(needed)))
