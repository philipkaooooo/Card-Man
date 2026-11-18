"""eBay's item details (card number, set, rarity, edition, finish, language, grading) as identity evidence.

The seller picks these from eBay's own lists, and they usually match the card. When they agree with the title and
the catalog, they settle the exact version, the same way a confirmed set code, rarity and edition would. When they
contradict the title, nothing is settled and the listing says why ("Check first"). Fakes and condition are still
for a person to judge from the photos: the details only say which card the seller listed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Hint:
    verified: dict = field(default_factory=dict)     # fields for the game's analyzer, as if confirmed
    conflict: str | None = None                      # a plain reason, when the details contradict the title
    other_game: str | None = None                    # eBay files it under the other game
    ignored: str | None = None                       # details set aside because the title is the better witness
    grade: tuple[str, str] | None = None             # (grader, grade) when eBay says it is graded
    language: str | None = None
    said: str = ""                                   # what the details say, for the confirmation note


# Never settled by eBay's details: special prints priced apart, error prints, other regions, reprints, several cards.
NOT_SETTLED = re.compile(r"prerelease|pre-release|\bstaff\b|\bstamp|\berror\b|misprint|miscut|\bno holo\b|asian|\baen\b|\(ae\)|"
                         r"\breprint|\bproxy|\bcustom|anniversary|\b25th\b|\b30th\b|celebration|classic|legendary collection|"
                         r"\bfan[\s-]*(?:made|art)|\bfans\s+art|\breplica|display card|\bdisplay\b", re.I)


def unsettled(title: str, sig: dict) -> str | None:
    """Why the details must not settle the version (or None)."""
    text = f"{title} {sig.get('edition') or ''} {sig.get('finish') or ''} {sig.get('rarity') or ''} {sig.get('_features') or ''}"
    m = NOT_SETTLED.search(text)
    return m.group(0).lower() if m else None


def _num(s: str | None) -> str:
    m = re.match(r"\s*#?\s*([A-Za-z]{0,5}-?\d{1,3}[a-z]?)", s or "")
    return (m.group(1).replace("-", "").lstrip("0").upper() or "0") if m else ""


def _words(s: str) -> set[str]:
    s = (s or "").lower().replace("'", "").replace("’", "")
    s = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", s)            # "#151MEWTWO" -> 151 mewtwo
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", s).split() if len(w) > 1}


def ygo(title: str, sig: dict, catalog) -> Hint:
    from .intel import claim_fits
    from .listing_parser import parse_listing_text
    h = Hint()
    if sig.get("game") == "pkm":
        h.other_game = "pkm"
        return h
    if sig.get("grader") and sig.get("grade"):
        h.grade = (sig["grader"], sig["grade"])
    lang = (sig.get("language") or "").lower()
    if lang.startswith("japan"):
        h.language = "ja"
    elif lang.startswith("english"):
        h.language = "en"
    code = (sig.get("number") or "").strip().upper()
    if not code or code not in catalog.by_code:
        return h                                         # no usable code: title evidence only
    if unsettled(title, sig):
        return h                                         # stamped, error, Asian-English or reprint: the title alone decides
    parsed = parse_listing_text(title)
    title_codes = [c for c in parsed.set_codes if c in catalog.by_code]
    if len(set(title_codes)) > 1 or parsed.is_lot:
        return h                                         # several cards in one listing
    if title_codes and code not in title_codes:
        fam_ids, fuzzy = catalog.family_matches(title)
        t_owners = {p.family_id for p in catalog.by_code[title_codes[0]]}
        if fam_ids and (fam_ids & t_owners):
            h.ignored = f"eBay's item details say {code}, but the title's {title_codes[0]} matches the card it names: going by the title."
            return h                                     # details copied from another listing: the title is the better witness
        h.conflict = f"The title says {title_codes[0]}, but eBay's item details say {code}. Check the photos."
        return h
    owners = {p.family_id for p in catalog.by_code[code]}
    fam_ids, fuzzy = catalog.family_matches(title)
    name = sig.get("name") or ""
    if fam_ids and not fuzzy and not (fam_ids & owners) and not title_codes:
        owner_words = set().union(*(_words(catalog.families[f].names.get("en") or "") for f in owners if f in catalog.families))
        if not (_words(name) & owner_words) or not (_words(title) & owner_words):
            names = ", ".join(sorted(catalog.families[f].names.get("en") or f for f in owners if f in catalog.families))
            h.conflict = f"eBay's item details say {code} ({names}), but the title names another card. Check the photos."
            return h
    h.verified["printed_set_code"] = code
    rars = {p.rarity for p in catalog.by_code[code]}
    if sig.get("rarity"):
        fit = [r for r in rars if claim_fits(sig["rarity"], r) or r.lower() == sig["rarity"].lower()]
        if len(fit) == 1:
            h.verified["verified_rarity"] = fit[0]
        elif not fit and len(rars) == 1:
            h.verified["verified_rarity"] = next(iter(rars))
        # A rarity eBay spells differently ("Utra Rare", "Secret") is not a contradiction: it is just not used.
    elif len(rars) == 1:
        h.verified["verified_rarity"] = next(iter(rars))
    ed = sig.get("edition")
    title_eds = parse_listing_text(title).editions
    if ed == "1st Edition":
        h.verified["verified_edition"] = "1st"
    elif ed == "Unlimited":
        h.verified["verified_edition"] = "Unlimited"
    elif len(title_eds) == 1:
        h.verified["verified_edition"] = title_eds[0]     # details silent, the title says it once: they agree
    if h.verified.get("verified_edition") and title_eds and h.verified["verified_edition"] not in title_eds:
        h.conflict = f"The title says {title_eds[0]}, but eBay's item details say {ed}. Check the edition stamp in the photos."
        h.verified = {}
        return h
    products = sorted({p.product_name or p.product_id for p in catalog.by_code[code]})
    if len(products) > 1:
        # One code, several products (an original and its reprint). Reprint words in eBay's Set or the title pick the
        # reprint; otherwise the original. The photo next to the reference art lets a person check.
        said = f"{sig.get('set') or ''} {title}".lower()
        rep = re.compile(r"25th|anniversary|reprint|legendary collection|speed duel|duel devastator|retro pack|tin|mega pack|collection")
        named = [p for p in products if any(w in said for w in _words(p) - {"of", "the", "and", "white", "blue", "eyes", "dragon"}) and rep.search(p.lower())]
        if named and rep.search(said):
            h.verified["verified_product"] = named[0]
        elif not rep.search(said):
            plain = [p for p in products if not rep.search(p.lower())]
            if len(plain) == 1:
                h.verified["verified_product"] = plain[0]
    if h.language:
        h.verified["card_language"] = h.language
    h.said = " · ".join(x for x in (code, h.verified.get("verified_rarity"), ed or (title_eds[0] if len(title_eds) == 1 else None)) if x)
    return h


def pkm(title: str, sig: dict, cat) -> Hint:
    from .pkm_intel import _NUM, _PROMO_NO, parse
    h = Hint()
    if sig.get("game") == "ygo":
        h.other_game = "ygo"
        return h
    if sig.get("grader") and sig.get("grade"):
        h.grade = (sig["grader"], sig["grade"])
    lang = (sig.get("language") or "").lower()
    if lang and not lang.startswith("english"):
        h.language = lang.split()[0]
    number = sig.get("number") or ""
    p = parse(title)
    if unsettled(title, sig) or p["stamped"] or p["lot"]:
        return h                                         # stamped, error or several cards: the title alone decides
    from .pkm_intel import analyze as _an
    alone = _an(title, cat)
    t_cards = {c.id.split(":")[2] for c in alone.identity.candidates}
    title_sure = len(t_cards) == 1 and bool(p["numbers"]) and not alone.has("CODE_NAME_MISMATCH")
    digits = lambda x: re.sub(r"\D", "", x).lstrip("0") or "0"
    if number:
        n_asp = digits(_num(number.split("/")[0]))                   # "SWSH136" and "136" are the same number
        t_nums = [digits(_num(a)) for a, _ in p["numbers"]]
        if t_nums and n_asp and n_asp not in t_nums:
            if title_sure:
                h.ignored = f"eBay's item details say {number}, but the title's number matches the card it names: going by the title."
                return h
            h.conflict = f"The title says card number {p['numbers'][0][0]}, but eBay's item details say {number}. Check the photos."
            return h
        if _NUM.search(number) or _PROMO_NO.search(number) or re.fullmatch(r"[A-Za-z]{0,5}\d{1,3}[a-z]?", number.strip()):
            h.verified["printed_set_code"] = number.strip()
    from .pkm_catalog import norm
    from .pkm_intel import _names_in
    name = sig.get("name") or ""
    # Only a title that names a card can contradict the details ("Old Pokemon Cards", "Charzard sir" name none).
    if name and title and _names_in(norm(title), cat) and not (_words(name) & _words(title)):
        if title_sure:
            h.ignored = f"eBay's item details name {name}, but the title's number and name agree: going by the title."
            h.verified = {}
            return h
        h.conflict = f"eBay's item details name {name}, but the title names another card. Check the photos."
        h.verified = {}
        return h
    version = None
    if sig.get("edition") in ("1st Edition", "Shadowless", "Unlimited"):
        version = sig["edition"]
    elif sig.get("finish") == "Reverse Holo":
        version = "Reverse Holo"
    t_versions = set(p["versions"])
    if version == "Unlimited" and "Shadowless" in t_versions:
        version = "Shadowless"                           # a Shadowless card is an unlimited print: the title is more exact
    if version == "Reverse Holo" and "Reverse Holo" not in t_versions and t_versions & {"1st Edition", "Shadowless"}:
        version = None
    if version and t_versions and version not in t_versions and version != "Reverse Holo" and t_versions & {"1st Edition", "Shadowless", "Unlimited"}:
        h.conflict = f"The title says {sorted(t_versions)[0]}, but eBay's item details say {version}. Check the photos."
        h.verified = {}
        return h
    if version:
        h.verified["verified_edition"] = version
    elif sig.get("finish") in ("Holo", "Non-holo") and "Reverse Holo" not in t_versions:
        h.verified["finish"] = sig["finish"]
    if sig.get("set"):
        h.verified["set_hint"] = sig["set"]
    if sig.get("rarity"):
        h.verified["rarity_hint"] = sig["rarity"]
    h.said = " · ".join(x for x in (sig.get("set"), number, sig.get("rarity"), version or sig.get("finish")) if x)
    return h
