"""Exact-identification intelligence over one listing: what is it, and what did the seller get wrong?

resolve() remains the only thing that can say RESOLVED. This layer adds the checks a careful buyer makes
against the catalog: wrong code for the named card, a rarity the code was never printed in, a cheap and an
expensive printing sharing a code, counterfeit language, photo text that disagrees with the seller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .identity import Catalog, IdentityResult, ListingSignals, normalize_name, parse_set_code, resolve
from .listing_parser import ParsedListing, parse_listing_text
from .models import IdentityStatus, Printing, Region

BLOCK, WARN, UPSIDE, INFO = "BLOCK", "WARN", "UPSIDE", "INFO"

# What a careful seller may truthfully call a printing's rarity. Short Prints are Commons on the card; older
# Prismatic Secret Rares were sold as "Secret Rare"; Parallel versions are still that rarity underneath.
CLAIM_COMPATIBLE = {"Common": {"Short Print", "Super Short Print"}, "Secret Rare": {"Prismatic Secret Rare"},
                    "Ultra Rare": {"Ultra Parallel Rare"}, "Super Rare": {"Super Parallel Rare"},
                    "Rare": {"Rare Parallel Rare"}}


def claim_fits(claim: str, rarity: str) -> bool:
    return claim == rarity or rarity in CLAIM_COMPATIBLE.get(claim, ())


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    text: str


@dataclass
class ListingAnalysis:
    parsed: ParsedListing
    identity: IdentityResult
    findings: list[Finding] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)   # candidate printing id -> probability
    original_codes: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(f.severity == BLOCK for f in self.findings)

    def has(self, *kinds: str) -> bool:
        return any(f.kind in kinds for f in self.findings)


_KANA = re.compile(r"[\u3040-\u30ff]")


def _weights(cands: list[Printing], parsed: ParsedListing) -> dict[str, float]:
    """Seller claims shift weight but never remove a candidate: sellers are wrong in both directions."""
    if not cands:
        return {}
    raw = {}
    for p in cands:
        w = 1.0
        if parsed.rarities:
            w *= 3.0 if any(claim_fits(c, p.rarity) for c in parsed.rarities) else 0.5
        if parsed.editions and p.edition:
            w *= 3.0 if p.edition in parsed.editions else 0.5
        if parsed.language == "ja":
            w *= 2.0 if p.region == Region.JP_OCG else 0.25
        elif parsed.language == "en":
            w *= 2.0 if p.region == Region.EN_TCG else 0.25
        elif _KANA.search(parsed.text):
            # A title written in Japanese is usually a Japanese card, but sellers there also sell English ones: a tilt only.
            w *= 2.0 if p.region == Region.JP_OCG else 0.5
        raw[p.id] = w
    t = sum(raw.values())
    return {k: v / t for k, v in raw.items()}


_JOINER = re.compile(r"\s(?:&|\+|and|w/|with|×|x)\s|[&+＆＋、]")


def _names_several_cards(title: str, catalog: Catalog) -> bool:
    """"Raigeki + Change of Heart & Monster Reborn" is three cards. Needs two different exact card names AND a joiner,
    so a single card whose name contains another ("Dark Magician Girl") or a title naming a character stays single."""
    if not _JOINER.search(title):
        return False
    fams, notes = catalog.family_matches(title)
    return len(fams) >= 2 and not notes


def _lot_only_from_product_name(text: str, parsed: ParsedListing, catalog: Catalog) -> bool:
    """"Original Artwork Collection" or "Legendary Collection" is a product, not a lot of cards."""
    from .listing_parser import LOT_CUES, _cues
    low = f" {text.lower()} "
    cues = set(_cues(low, LOT_CUES))
    if cues - {"collection"} or (parsed.lot_size or 0) >= 2 or len(parsed.set_codes) >= 3:
        return False
    names = catalog.__dict__.get("_collection_products")
    if names is None:
        names = {(p.product_name or "").lower() for p in catalog.printings if "collection" in (p.product_name or "").lower()}
        catalog.__dict__["_collection_products"] = names
    return any(n and n in low for n in names)


def analyze(title: str, catalog: Catalog, *, description: str = "", verified: dict | None = None,
            ocr_codes: tuple[str, ...] = ()) -> ListingAnalysis:
    """`verified` holds ListingSignals fields a person or the photos confirmed (printed_set_code, verified_*).
    `ocr_codes` are codes machine-read from photos: strong hints that still need a person's confirmation."""
    verified = dict(verified or {})
    parsed = parse_listing_text(f"{title}\n{description}" if description else title)
    title_codes = parse_listing_text(title).set_codes
    if parsed.is_lot and _lot_only_from_product_name(f"{title} {description}", parsed, catalog):
        parsed = replace(parsed, is_lot=False)
    if not parsed.is_lot and _names_several_cards(title, catalog):
        parsed = replace(parsed, is_lot=True)
    findings: list[Finding] = []

    if parsed.counterfeit_cues:
        findings.append(Finding("COUNTERFEIT_CUE", BLOCK, "Listing text suggests a non-genuine card: "
                                + ", ".join(parsed.counterfeit_cues)))
    if parsed.not_a_card_cues and not parsed.set_codes:
        findings.append(Finding("NOT_A_CARD", BLOCK, "Looks like an accessory, not a card: " + ", ".join(parsed.not_a_card_cues)))
    # Rush Duel is a different game: its codes carry "RD/" and its foils are "Rush Rare". Never a Duel Monsters lead,
    # whatever card name the title also matches.
    if any(c.upper().startswith("RD/") for c in parsed.set_codes) or re.search(r"rush\s*(?:duel|rare)|ラッシュ", parsed.text, re.I):
        findings.append(Finding("OUT_OF_SCOPE", BLOCK, "Rush Duel card: a separate game, not Duel Monsters."))
    if parsed.language in ("ko", "zh"):
        findings.append(Finding("OUT_OF_SCOPE", BLOCK, f"Language '{parsed.language}' is outside JP OCG / EN TCG."))
    if parsed.language == "asian_en":
        findings.append(Finding("OUT_OF_SCOPE", WARN, "Described as Asian-English: quarantined, never comparable to EN TCG."))
    if len(parsed.editions) > 1:
        findings.append(Finding("EDITION_CONFLICT", WARN, f"Text claims several editions: {', '.join(parsed.editions)}."))

    # Codes the catalog does not know are often typos; only known codes count as the seller's claim.
    known = [c for c in (title_codes or parsed.set_codes) if c in catalog.by_code]
    for c in (title_codes or parsed.set_codes):
        if c not in catalog.by_code and parse_set_code(c).get("lang") in ("EN", "JP", ""):
            findings.append(Finding("UNKNOWN_CODE", WARN, f"Set code {c} is not in the catalog: typo, or a print the source lacks."))
    seller_code = known[0] if known else None
    if len(known) > 1 and not parsed.is_lot:
        findings.append(Finding("SEVERAL_CODES", WARN, f"Several set codes in one listing: {', '.join(known)}."))

    sig_title = title
    fam_ids, fuzzy = catalog.family_matches(title)
    # Named card vs the code's real owner: the classic misidentification, in either direction.
    if seller_code:
        owners = {p.family_id for p in catalog.by_code[seller_code]}
        # A typo-matched name ("B. Dragon" ~ "Baby Dragon") is too weak to accuse the seller of a wrong code.
        if fam_ids and not fuzzy and not (fam_ids & owners):
            owner_names = ", ".join(sorted(catalog.families[f].names.get("en") or catalog.families[f].names.get("ja", f)
                                           for f in owners if f in catalog.families))
            findings.append(Finding("CODE_NAME_MISMATCH", WARN,
                                    f"{seller_code} belongs to {owner_names}, not the card named in the title. "
                                    "Seller has the name or the code wrong: check the photo."))
        elif not fam_ids:
            # Title has only a code: identity can still start from the code's owner.
            first = next(iter(owners))
            fam = catalog.families.get(first)
            if fam:
                sig_title = f"{title} {fam.names.get('en') or fam.names.get('ja')}"

    if ocr_codes:
        ocr_known = [c for c in ocr_codes if c in catalog.by_code]
        if seller_code and ocr_known and seller_code not in ocr_known:
            findings.append(Finding("PHOTO_CONTRADICTS_SELLER", UPSIDE,
                                    f"Photo text reads {', '.join(ocr_known)} but the seller wrote {seller_code}."))
        elif ocr_known and not seller_code:
            findings.append(Finding("PHOTO_ADDS_CODE", UPSIDE, f"Seller gave no code; photo text reads {', '.join(ocr_known)}."))

    photo_code = next((c for c in ocr_codes if c in catalog.by_code), None)
    sig = {"title": sig_title, "seller_set_code": photo_code or seller_code,
           "seller_rarity": parsed.rarities[0] if parsed.rarities else None,
           "seller_edition": parsed.editions[0] if len(parsed.editions) == 1 else None,
           "slab_cert": parsed.cert, "is_multi_card": parsed.is_lot}
    sig.update({k: v for k, v in verified.items() if v is not None})
    identity = resolve(ListingSignals(**sig), catalog)

    cands = identity.candidates
    code_cands = catalog.by_code.get(photo_code or seller_code or "", [])
    if code_cands and not parsed.is_lot:
        if parsed.rarities:
            have = {p.rarity for p in code_cands}
            missing = [r for r in parsed.rarities if not any(claim_fits(r, h) for h in have)]
            if missing and len(missing) == len(parsed.rarities):
                findings.append(Finding("RARITY_NOT_ON_CODE", WARN,
                                        f"{code_cands[0].set_code} was printed as {', '.join(sorted(have))}, "
                                        f"never as {', '.join(missing)}. Rarity is wrong, or the code is."))
        else:
            rar = sorted({p.rarity for p in code_cands})
            if len(rar) > 1:
                findings.append(Finding("RARITY_UNSTATED", UPSIDE, f"Seller gave no rarity and {code_cands[0].set_code} exists as "
                                        f"{', '.join(rar)}. A foil check could reveal a far better card."))
        products = sorted({p.product_name or p.product_id for p in code_cands})
        if len(products) > 1:
            findings.append(Finding("REPRINT_SHARES_CODE", WARN,
                                    f"{code_cands[0].set_code} is shared by {len(products)} products ({'; '.join(products[:3])}). "
                                    "An original and a reprint can look alike in the listing."))
    if parsed.reprint_cues and any("anniversary" not in (p.product_name or "").lower() for p in cands) and parsed.vintage_cues:
        findings.append(Finding("REPRINT_LANGUAGE", WARN, "Text mentions both vintage and reprint wording."))
    if parsed.ignorance_cues:
        findings.append(Finding("SELLER_UNSURE", UPSIDE, "Seller says they do not know the item: "
                                + ", ".join(parsed.ignorance_cues) + ". Mispricing is more likely, and so is misdescription."))
    if parsed.cert:
        findings.append(Finding("CERT_PRESENT", INFO, f"Cert {parsed.cert}: a lookup is not proof the slab or card is genuine."))
    if identity.status == IdentityStatus.CONFLICT:
        findings.append(Finding("IDENTITY_CONFLICT", BLOCK, "; ".join(identity.blocking_reasons)))
    if identity.status == IdentityStatus.QUARANTINED:
        findings.append(Finding("QUARANTINED", BLOCK, "; ".join(identity.blocking_reasons)))
    return ListingAnalysis(parsed, identity, findings, _weights(cands, parsed))


def lot_contents(text: str, catalog: Catalog) -> list[dict]:
    """Best-effort itemization of a lot, line by line or comma by comma. Each row stays a claim."""
    import re
    rows = []
    for seg in re.split(r"[\n,、，/;；・&＆+＋]+", text):
        seg = seg.strip()
        if len(seg) < 3:
            continue
        p = parse_listing_text(seg)
        code = next((c for c in p.set_codes if c in catalog.by_code), None)
        if code:
            cands = catalog.by_code[code]
        else:
            fams, notes = catalog.family_matches(seg)
            if not fams or notes or len(fams) > 1:
                continue
            cands = [c for f in fams for c in catalog.by_family.get(f, [])]
        if p.rarities:
            narrowed = [c for c in cands if c.rarity in p.rarities]
            cands = narrowed or cands
        rows.append({"segment": seg, "code": code, "rarity_claim": p.rarities[0] if p.rarities else None,
                     "candidates": [c.id for c in cands]})
    return rows
