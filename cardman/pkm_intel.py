"""What a Pokémon listing title says, and which catalog versions it could be.

Reads: card number ("199/165", "#199", "TG30/TG30", "SWSH050"), set name or abbreviation ("151", "MEW", "Paldean Fates"),
card name (the longest catalog name in the title), rarity words (SIR, IR, Full Art, Alt Art, Secret, Gold, Rainbow, Hyper,
Shiny), version words (Reverse Holo, Holo, 1st Edition, Shadowless, Unlimited, Promo), grading, condition, language.
Never trusts one claim alone: a number that belongs to another card is a warning, not a verdict.

Blocks: proxies, customs, fakes, orica, metal or gold "cards", jumbo / oversized cards, code cards, non-English cards
(v1 covers the English TCG only). Sealed product (packs, boxes, tins, ETBs) is its own kind and never priced as a single.
"""
from __future__ import annotations

import re
import unicodedata

from .identity import IdentityResult
from .intel import BLOCK, INFO, UPSIDE, WARN, Finding, ListingAnalysis
from .listing_parser import _CERT_RE, _GRADE_RE, ParsedListing
from .models import IdentityStatus
from .pkm_catalog import PkmCatalog, norm

FAKE = ("proxy", "custom", "orica", "fan art", "fanart", "replica", "fake", "not real", "unofficial", "metal card", "gold metal",
        "metal pokemon", "24k", "gold plated", "gold foil card", "reprint card", "handmade", "playtest")
# "art card" alone is a custom display card; "Alt Art Card" / "Full Art Card" are real cards.
ART_CARD = re.compile(r"(?<!alt )(?<!full )(?<!alternate )(?<!alt-)\bart card")
JUMBO = ("jumbo", "oversized", "oversize", "big card", "large card", "giant card", "burger king", "gold card", "gold cards")
REPRINT = r"celebrations|classic collection|pok[eé]mon tcg classic|\bclassic\b.*\b(?:deck|box)\b|25th anniversary"
CODE = ("code card", "online code", "ptcgo code", "tcg live code", "tcgo code", "digital code", "unused code")
SEALED = ("booster box", "booster pack", "booster bundle", "elite trainer box", "etb", "blister", "sealed pack", "sealed box",
          "collection box", "premium collection", "build & battle", "build and battle", "booster display", "tin", "factory sealed",
          "sealed case", "theme deck")
LOT = ("lot", "bundle", "bulk", "collection of", "cards total", "random cards", "mystery")
OTHER_LANG = {"japanese": "ja", "japan": "ja", "jpn": "ja", "日本": "ja", "korean": "ko", "chinese": "zh", "thai": "th",
              "indonesian": "id", "indonesia": "id", "s-chinese": "zh", "t-chinese": "zh", "simplified chinese": "zh",
              "traditional chinese": "zh", "thailand": "th", "german": "de", "deutsch": "de", "french": "fr", "francais": "fr", "italian": "it", "italiano": "it",
              "spanish": "es", "espanol": "es", "portuguese": "pt"}
RARITY = (("Special Illustration Rare", r"\bsir\b|special illustration"), ("Illustration Rare", r"(?<!special )\bir\b|(?<!special )illustration rare"),
          ("Hyper Rare", r"hyper rare|\bgold\b(?! star)|\bhr\b"), ("Secret Rare", r"secret rare|\bsecret\b"),
          ("Rare Rainbow", r"rainbow"), ("Full Art", r"full art|\bfa\b"), ("Alternate Art", r"alt(?:ernate)? art|\baa\b"),
          ("Shiny", r"shiny"), ("Gold Star", r"gold star"), ("ACE SPEC", r"ace spec"), ("Trainer Gallery", r"trainer gallery|\btg\d"),
          ("Double Rare", r"double rare"), ("Ultra Rare", r"ultra rare"))
VERSION = (("Reverse Holo", r"reverse\s*holo|rev(?:erse)?\s*foil|\brh\b"), ("1st Edition", r"1st\s*ed(?:ition)?|first edition"),
           ("Shadowless", r"shadowless"), ("Unlimited", r"\bunlimited\b"), ("Non-holo", r"non[\s-]*holo"),
           ("Holo", r"(?<!reverse )(?<!non )(?<!non-)\bholo(?:foil)?\b"), ("Promo", r"\bpromo\b"))
STAMP = r"prerelease|pre-release|staff|league|stamped|stamp|winner|championship|worlds"
CONDITION = (("NM", r"\bnm\b|near mint|\bmint\b"), ("LP", r"\blp\b|lightly played"), ("MP", r"\bmp\b|moderately played"),
             ("HP", r"\bhp\b(?!\s*\d)|heavily played"), ("DMG", r"\bdmg\b|damaged|creased"))
_NUM = re.compile(r"(?<![\w/])#?\s*([A-Z]{0,4}\d{1,3}[a-z]?)\s*/\s*([A-Z]{0,4}\d{1,3})(?![\w/])", re.I)
_PROMO_NO = re.compile(r"\b(SWSH|SM|XY|BW|SVP|SV-P|DP|HGSS)\s*-?\s*#?\s*(\d{2,3})\b", re.I)
_HASH = re.compile(r"#\s*(\d{1,3})\b")


def _has(low: str, words) -> tuple[str, ...]:
    return tuple(w for w in words if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low))


def parse(title: str) -> dict:
    t = unicodedata.normalize("NFKC", title or "")
    low = f" {t.lower()} "
    g = _GRADE_RE.search(t)
    gc, gr = (g.group(1).upper().replace("BECKETT", "BGS"), g.group(2)) if g else (None, None)
    nums = [(m.group(1), m.group(2)) for m in _NUM.finditer(t) if not re.fullmatch(r"\d{1,2}", m.group(2)) or int(m.group(2)) >= 10]
    promo = [(f"{m.group(1).upper().replace('-', '')}{m.group(2)}", None) for m in _PROMO_NO.finditer(t)]
    hashes = [(m.group(1), None) for m in _HASH.finditer(t)] if not nums else []
    langs = {v for k, v in OTHER_LANG.items() if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low) or (k == "日本" and k in t)}
    if re.search(r"[぀-ヿ]", t):
        langs.add("ja")
    lot_n = re.search(r"\b(\d{2,4})\s*(?:\+\s*)?(?:cards|card lot|pc|pcs)\b", low)
    return {
        "text": t, "low": low, "grade_company": gc, "grade": gr, "cert": (m.group(1) if (m := _CERT_RE.search(t)) else None),
        "numbers": nums + promo + hashes,
        "rarities": tuple(n for n, p in RARITY if re.search(p, low)),
        "versions": tuple(n for n, p in VERSION if re.search(p, low)),
        "stamped": bool(re.search(STAMP, low)),
        "condition": None if gc else next((n for n, p in CONDITION if re.search(p, low)), None),
        "fake": _has(low, FAKE) + (("art card",) if ART_CARD.search(low) else ()), "jumbo": _has(low, JUMBO), "code": _has(low, CODE),
        "sealed": _has(low, SEALED) if not nums else (),
        "lot": bool(_has(low, LOT) or (lot_n and int(lot_n.group(1)) > 1)), "lot_size": int(lot_n.group(1)) if lot_n else None,
        "languages": tuple(sorted(langs)),
    }


def _names_in(title_norm: str, cat: PkmCatalog) -> list[str]:
    """Catalog names found in the title, longest first (word n-grams up to six words)."""
    words = title_norm.split()
    found = set()
    for n in range(min(6, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            g = " ".join(words[i:i + n])
            if g in cat.by_name and not (n == 1 and len(g) < 4):
                found.add(g)
    return sorted(found, key=lambda s: (-len(s.split()), -len(s)))


def _set_of(p: dict, cat: PkmCatalog) -> str | None:
    tn = f" {norm(p['text'])} "
    for sname, sid in sorted(cat.set_names.items(), key=lambda kv: -len(kv[0])):
        if len(sname) >= 3 and f" {sname} " in tn:
            return sid
    for w in re.findall(r"\b[A-Z]{3,4}\b", p["text"]):
        if w in cat.set_codes and w not in ("PSA", "BGS", "CGC", "SGC", "TCG", "HOLO", "RARE"):
            return cat.set_codes[w]
    return None


def analyze(title: str, cat: PkmCatalog, *, description: str = "", verified: dict | None = None, ocr_codes=()) -> ListingAnalysis:
    verified = dict(verified or {})
    if verified.get("printed_set_code"):              # the number a person read off the card settles which card it is
        title = f"{title} {verified['printed_set_code']}"
    p = parse(f"{title} {description}" if description else title)
    if verified.get("printed_set_code"):
        n = _NUM.search(str(verified["printed_set_code"]))
        p["numbers"] = [(n.group(1), n.group(2))] if n else p["numbers"]
    findings: list[Finding] = []
    if p["fake"]:
        findings.append(Finding("COUNTERFEIT_CUE", BLOCK, "Not a real card (" + ", ".join(p["fake"]) + ")."))
    if p["jumbo"]:
        findings.append(Finding("NOT_A_CARD", BLOCK, "A jumbo or oversized card, not a standard single."))
    if p["code"]:
        findings.append(Finding("NOT_A_CARD", BLOCK, "An online code card, not a collectible single."))
    if p["sealed"] and not p["grade_company"]:
        findings.append(Finding("SEALED_PRODUCT", BLOCK, "Sealed product (" + ", ".join(p["sealed"]) + "): we only price single cards."))
    other = [l for l in p["languages"] if l != "en"]
    if other:
        findings.append(Finding("OUT_OF_SCOPE", BLOCK, "Not an English card: only English Pokémon cards are covered so far."))
    if p["stamped"]:
        findings.append(Finding("STAMPED", WARN, "A stamped or special version (prerelease, staff, league, event): priced apart "
                                "from the normal card, and not tracked yet."))

    reprint = bool(re.search(REPRINT, p["low"]))
    tn = norm(p["text"])
    names = _names_in(tn, cat)
    by_number: list[str] = []
    for num, tot in p["numbers"]:
        n = num.lstrip("0").lower() or "0"
        for cid in cat.by_number.get(n, []):
            s = cat.set(cat._cards[cid][3]) or {}
            if tot is None or (tot.isdigit() and s.get("printed_total") == int(tot)) or (not tot.isdigit() and tot.upper()[:2] == num.upper()[:2]):
                by_number.append(cid)
    sid = _set_of(p, cat)
    if reprint:
        # Celebrations / Classic reprints reuse the old number (Charizard 4/102): never the 1999 card.
        rs = [x for x in ("cel25c", "cel25") if cat.set(x)]
        by_number = [c for c in by_number if cat._cards[c][3] in rs]
        findings.append(Finding("REPRINT", WARN, "A later reprint (Celebrations or Classic), not the original card: priced apart."))
        sid = sid if sid in rs else None
    def name_ok(cid: str) -> bool:
        """The title names this card: its exact name, or the name without the ex / V / GX / VMAX ending sellers drop."""
        cn = cat._cards[cid][2]
        cw = set(cn.split())
        return any(cn == n or cn.startswith(n + " ") or n.startswith(cn + " ") or (len(n.split()) > 1 and set(n.split()) <= cw)
                   for n in names) or (len(cw) > 1 and cw <= set(tn.split()))

    cards: list[str]
    weak_number = all(t is None for _, t in p["numbers"]) and not _PROMO_NO.search(p["text"])
    if by_number:
        # The number decides the card; the name only confirms it. The set word is a tie-break, never a veto
        # ("Zapdos 15/62 Base Set" is the Fossil Zapdos: sellers call every early set "Base Set").
        match = [c for c in by_number if name_ok(c)]
        if match:
            cards = [c for c in match if sid and cat._cards[c][3] == sid] or match
        elif names and weak_number:
            named = [cid for nm in names[:1] for cid in cat.by_name.get(nm, [])]
            cards = [c for c in named if sid and cat._cards[c][3] == sid] or named    # a bare "#256" is weaker than the name
        elif names and not reprint and not other:          # a Japanese or Chinese card's number follows other sets
            pool = [c for c in by_number if sid and cat._cards[c][3] == sid] or by_number
            owner = ", ".join(sorted({cat.card_name(c) for c in pool}))
            findings.append(Finding("CODE_NAME_MISMATCH", WARN, f"Card number {p['numbers'][0][0]}"
                                    f"{'/' + p['numbers'][0][1] if p['numbers'][0][1] else ''} belongs to {owner}, not the card named "
                                    "in the title. The seller has the name or the number wrong: check the photo."))
            named = [cid for nm in names[:1] for cid in cat.by_name.get(nm, [])]
            cards = pool + ([c for c in named if sid and cat._cards[c][3] == sid] or named)[:6]
        else:
            cards = [c for c in by_number if sid and cat._cards[c][3] == sid] or by_number
    else:
        named = [cid for nm in names[:1] for cid in cat.by_name.get(nm, [])]
        if reprint:
            named = [c for c in named if cat._cards[c][3] in ("cel25c", "cel25")]
        cards = [c for c in named if sid and cat._cards[c][3] == sid] or named
    if len(cards) > 40:                        # a bare name like "Pikachu": too many to weigh
        cards = sorted(cards, key=lambda c: cat._cards[c][9] or "", reverse=True)[:40]

    cands = [v for c in cards for v in cat.versions(c)]
    if verified and verified.get("verified_edition"):
        cands = [v for v in cands if (v.edition or "").lower().startswith(str(verified["verified_edition"]).lower())] or cands
    weights = _weights(cands, p)
    status = (IdentityStatus.RESOLVED if len(cands) == 1 and not any(f.severity == BLOCK for f in findings)
              else IdentityStatus.CANDIDATES if cands else IdentityStatus.UNRESOLVED)
    identity = IdentityResult(status, sorted(cands, key=lambda v: -weights.get(v.id, 0)), evidence=[f"pkm title parse: {len(cards)} card(s)"])
    if p["cert"]:
        findings.append(Finding("CERT_PRESENT", INFO, f"Cert {p['cert']}: a lookup is not proof the slab or card is genuine."))
    if len(cards) > 1 and not p["rarities"] and len({cat._cards[c][6] for c in cards}) > 1:
        findings.append(Finding("RARITY_UNSTATED", UPSIDE, "No rarity given, and the card it could be exists in several rarities."))
    parsed = ParsedListing(text=p["text"], set_codes=tuple(f"{a}/{b}" if b else a for a, b in p["numbers"]), rarities=p["rarities"],
                           editions=tuple(v for v in p["versions"] if v in ("1st Edition", "Unlimited", "Shadowless", "Reverse Holo", "Holo")),
                           grade_company=p["grade_company"], grade=p["grade"], cert=p["cert"], condition=p["condition"],
                           language="en" if not other else other[0], is_lot=p["lot"] and not p["sealed"], lot_size=p["lot_size"],
                           counterfeit_cues=p["fake"], not_a_card_cues=p["jumbo"] + p["code"])
    return ListingAnalysis(parsed, identity, findings, weights)


_RARITY_FITS = {"Special Illustration Rare": ("special illustration",), "Illustration Rare": ("illustration rare",),
                "Hyper Rare": ("hyper", "gold", "secret"), "Secret Rare": ("secret", "hyper", "rainbow", "gold"),
                "Rare Rainbow": ("rainbow",), "Full Art": ("ultra", "full art", "secret"), "Alternate Art": ("alt", "secret", "illustration"),
                "Shiny": ("shiny", "shining"), "Gold Star": ("star",), "ACE SPEC": ("ace spec",), "Trainer Gallery": ("trainer gallery",),
                "Double Rare": ("double rare",), "Ultra Rare": ("ultra",)}


def _weights(cands, p) -> dict[str, float]:
    """Claims tilt the weights, never remove a candidate. Version words weigh most: they decide the price."""
    if not cands:
        return {}
    vs = set(p["versions"])
    raw = {}
    for v in cands:
        w = 1.0
        ed = v.edition or ""
        if "Reverse Holo" in vs:
            w *= 4.0 if ed == "Reverse Holo" else 0.4
        elif ed == "Reverse Holo":
            w *= 0.35                      # unstated reverse holo is usually the normal card
        if "1st Edition" in vs:
            w *= 4.0 if ed.startswith("1st Edition") else 0.3
        elif ed.startswith("1st Edition"):
            w *= 0.4                       # 1st Edition is always advertised when it is one
        if "Shadowless" in vs:
            w *= 4.0 if ed == "Shadowless" else 0.4
        elif ed == "Shadowless":
            w *= 0.5
        if "Unlimited" in vs and ed.startswith("Unlimited"):
            w *= 2.0
        if "Holo" in vs and ed in ("Holo", "1st Edition Holo", "Unlimited Holo"):
            w *= 1.5
        if "Non-holo" in vs and ed in ("Normal", "1st Edition", "Unlimited"):
            w *= 2.0
        if p["rarities"]:
            r = (v.rarity or "").lower()
            fits = any(k in r for claim in p["rarities"] for k in _RARITY_FITS.get(claim, (claim.lower(),)))
            w *= 3.0 if fits else 0.5
        raw[v.id] = w
    s = sum(raw.values())
    return {k: x / s for k, x in raw.items()}
