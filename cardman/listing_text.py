"""Read a raw marketplace listing (English or Japanese) into seller-side signals and risk flags.

Everything extracted here is what the SELLER wrote. It feeds the resolver's seller_* fields, which
never narrow identity on their own; only image-verified fields do. The value of this module is in
noticing things: a rarity the code was never printed in, a lot sold as a single, a proxy, a stock photo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .identity import ListingSignals, parse_set_code

PARSER_VERSION = "listing-text-0.1.0"

# Canonical rarity names as the identity source spells them, with seller spellings (EN and JP).
# Order matters: longer / more specific names first, so "Ultra Parallel" is not read as "Ultra".
RARITY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("Quarter Century Secret Rare", (r"quarter\s*century(\s*secret)?", r"\bqcsr\b", r"\b25th\s*secret",
                                     r"クォーターセンチュリー", r"クオーターセンチュリー", r"クオシク", r"25thシク")),
    ("Platinum Secret Rare", (r"platinum\s*secret", r"\bplsr\b", r"プラチナシークレット", r"プラシク")),
    ("Prismatic Secret Rare", (r"prismatic\s*secret", r"\bprsr\b", r"プリズマティックシークレット", r"プリシク")),
    ("20th Secret Rare", (r"\b20th\s*secret", r"20thシークレット", r"20thシク")),
    ("Extra Secret Rare", (r"extra\s*secret", r"エクストラシークレット", r"エクシク")),
    ("Gold Secret Rare", (r"gold\s*secret", r"ゴールドシークレット", r"ゴルシク")),
    ("Premium Gold Rare", (r"premium\s*gold", r"プレミアムゴールド", r"プレゴル")),
    ("Starlight Rare", (r"star\s*light", r"\bstlr\b", r"スターライト")),
    ("Ultimate Rare", (r"ultimate\s*rare", r"\butr\b", r"\bultimate\b", r"レリーフ", r"アルティメット")),
    ("Ghost Rare", (r"ghost\s*rare", r"\bghost\b",)),
    ("Holographic Rare", (r"holographic\s*rare", r"ホログラフィック", r"ホロレア", r"(?<![ァ-ヴ])ホロ(?![ァ-ヴ])")),
    ("Collector's Rare", (r"collector'?s\s*rare", r"コレクターズレア", r"コレクターズ", r"コレレア")),
    ("Millennium Rare", (r"millennium\s*rare", r"ミレニアム")),
    ("Gold Rare", (r"gold\s*rare", r"ゴールドレア")),
    ("Ultra Parallel Rare", (r"ultra\s*parallel", r"ウルトラパラレル", r"ウルパラ")),
    ("Super Parallel Rare", (r"super\s*parallel", r"スーパーパラレル", r"スーパラ")),
    ("Normal Parallel Rare", (r"normal\s*parallel", r"ノーマルパラレル", r"ノーパラ")),
    ("Secret Rare", (r"secret\s*rare", r"\bscr\b", r"\bsecret\b", r"シークレット", r"シク(?!レ)")),
    ("Ultra Rare", (r"ultra\s*rare", r"\bur\b", r"ウルトラ", r"ウルレア")),
    ("Super Rare", (r"super\s*rare", r"\bsr\b(?!\s*-?\d)", r"スーパーレア", r"スーパー")),
    ("Rare", (r"\brare\b", r"(?<![ァ-ヴー])レア(?!リティ)")),
    ("Common", (r"\bcommon\b", r"(?<![ァ-ヴー])ノーマル(?!パラ)")),
]

_EDITION = [("1st", r"\b1st(\s*ed(ition|\.)?)?\b|\bfirst\s*edition\b"),
            ("Limited", r"\blimited\s*edition\b"),
            ("Unlimited", r"\bunlimited\b|\bunl\b")]

_GRADE_RE = re.compile(r"\b(PSA|BGS|CGC|SGC|ARS|BECKETT)\s*[-:#]?\s*(10\+|10|[1-9](?:\.5)?)\b", re.I)
_CONDITION = [("NM", r"\bnear\s*mint\b|\bnm\b|極美品|未使用"), ("LP", r"\blightly\s*played\b|\blp\b|美品"),
              ("MP", r"\bmoderately\s*played\b|\bmp\b"), ("HP", r"\bheavily\s*played\b|\bhp\b|プレイ用"),
              ("DMG", r"\bdamaged\b|\bdmg\b|傷あり|キズあり|折れ")]

# Phrases that change what you are actually buying. (code, regex, what it means)
_RISKS: list[tuple[str, str, str]] = [
    ("proxy", r"\bproxy\b|\borica\b|オリカ|\bcustom\b|\bfan\s*made\b|\breplica\b|\breproduction\b|\bunofficial\b|"
              r"\bnot\s+(an?\s+)?(original|authentic|real)\b|\bfake\b|\bnon[- ]official\b|コピー品|レプリカ",
     "Seller text says proxy / custom / replica: not a genuine card."),
    ("asian_english", r"asian[\s-]*english|アジア版|亜英|\bae\b",
     "Asian-English print: not English TCG, quarantined."),
    ("korean", r"\bkorean\b|韓国版", "Korean print: out of scope."),
    ("stock_photo", r"stock\s*(photo|image)|example\s*(photo|image)|sample\s*(photo|image)|画像はイメージ|イメージ画像|"
                    r"photo\s*(is\s*)?for\s*reference|not\s*the\s*actual",
     "Photo is not the item: condition, edition and authenticity cannot be judged from it."),
    ("no_returns", r"\bno\s*returns?\b|返品不可|ノークレーム|ノーリターン|nc\s*nr",
     "No returns: a wrong or fake card is your loss."),
    ("damage_hint", r"\bcrease[ds]?\b|\bbent\b|\bwater\s*damage|\bscratch(es|ed)?\b|折れ|傷|キズ|汚れ|日焼け",
     "Seller mentions damage: price against played or damaged sales, not NM."),
    ("reprint_hint", r"\breprint\b|再録", "Seller says reprint: value it as the reprint, not the original."),
    ("graded_slab_only", r"\bslab\s*only\b|\bempty\s*slab\b|\bcase\s*only\b|ケースのみ",
     "Slab or case only: may not include the card."),
    ("unopened_lottery", r"\bmystery\b|\brandom\b|\bgrab\s*bag\b|オリパ|くじ",
     "Mystery or lottery product: no specific card is being sold."),
]

_LOT_RE = re.compile(r"\blot\b|\bbundle\b|\bplayset\b|\bset\s+of\s+\d|\b(\d{1,3})\s*x\b|\bx\s*(\d{1,3})\b|"
                     r"(\d{1,4})\s*枚|まとめ|セット売り|\b(\d{1,3})\s*(cards|pcs|pieces)\b", re.I)
_COLLECTION_RE = re.compile(r"\bbinder\b|\bcollection\b|\bbulk\b|引退|ファイル|ストレージ|\bbox\s*of\b", re.I)
_JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")


@dataclass
class Flag:
    code: str
    text: str            # what in the listing triggered it
    meaning: str


@dataclass
class ParsedListing:
    title: str
    body: str
    signals: ListingSignals
    rarity_claims: list[str] = field(default_factory=list)
    edition_claim: str | None = None
    grade_company: str | None = None
    grade: str | None = None
    condition: str | None = None
    quantity: int = 1
    is_lot: bool = False
    is_collection: bool = False
    language_hint: str | None = None         # "ja" / "en": what the TEXT suggests, not the card
    language_claims: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    @property
    def flag_codes(self) -> set[str]:
        return {f.code for f in self.flags}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


_RARE_TAIL = re.compile(r"\s*(rare\b|レア)")


def find_rarities(text: str) -> list[str]:
    """Rarity names the text claims, most specific first, without double-reading a longer phrase."""
    t = _norm(text).lower()
    found: list[str] = []
    for name, pats in RARITY_PATTERNS:
        hit = False
        for pat in pats:
            for m in list(re.finditer(pat, t)):
                hit = True
                end = m.end()
                tail = _RARE_TAIL.match(t, end)          # "ultra parallel| rare": the trailing word is part of it
                end = tail.end() if tail else end
                t = t[:m.start()] + " " * (end - m.start()) + t[end:]   # consume every spelling of it
        if hit:
            found.append(name)
    return found


def parse_listing(title: str, body: str = "") -> ParsedListing:
    title_n, body_n = _norm(title), _norm(body)
    text = f"{title_n}\n{body_n}"
    low = text.lower()
    flags: list[Flag] = []

    code = parse_set_code(title_n) or parse_set_code(body_n)
    seller_code = code["code"] if code and code["kind"] == "set_code" else None
    cert = None
    for m in re.finditer(r"(?:cert|証明|#)\s*[:#]?\s*(\d{7,10})\b", text, re.I):
        cert = m.group(1)

    rarities = find_rarities(title_n) or find_rarities(body_n)
    edition = next((ed for ed, pat in _EDITION if re.search(pat, low)), None)
    g = _GRADE_RE.search(text)
    grade_co, grade = (("BGS" if g.group(1).upper() == "BECKETT" else g.group(1).upper()), g.group(2)) if g else (None, None)
    if grade_co is None and re.search(r"鑑定|graded", low):
        flags.append(Flag("graded_unclear", "graded", "Says graded but no company and grade found: ask for the label."))
    cond = next((c for c, pat in _CONDITION if re.search(pat, low)), None)

    qty, is_lot = 1, False
    for m in _LOT_RE.finditer(text):
        is_lot = True
        n = next((int(x) for x in m.groups() if x), None)
        if n and n > 1:
            qty = max(qty, n)
    # "x1" / "1枚" is a single.
    if is_lot and qty == 1 and re.search(r"\bx\s*1\b|\b1\s*x\b|(?<!\d)1\s*枚", text, re.I) and not re.search(r"\blot\b|まとめ|bundle", low):
        is_lot = False
    is_collection = bool(_COLLECTION_RE.search(text))
    if is_lot:
        flags.append(Flag("lot", f"quantity {qty}" if qty > 1 else "lot wording",
                          "More than one card: value each card separately, never as a single sale."))
    if is_collection:
        flags.append(Flag("collection", _COLLECTION_RE.search(text).group(0),
                          "Collection / binder: worth a card-by-card look, often underpriced as a whole."))

    claims = []
    if re.search(r"japanese|\bjp\b|\bjpn\b|日本語版|日版|\bocg\b", low):
        claims.append("ja")
    if re.search(r"\benglish\b|英語版|英語|\btcg\b", low):
        claims.append("en")
    lang_hint = "ja" if _JP_CHARS.search(title_n) else ("en" if title_n.strip() else None)
    if code and code.get("region") is not None:
        code_lang = "ja" if code["region"].value == "JP_OCG" else "en"
        other = [c for c in claims if c != code_lang]
        if other and code_lang not in claims:
            flags.append(Flag("language_conflict", f"{seller_code} vs '{other[0]}'",
                              f"Set code {seller_code} is a {'Japanese' if code_lang == 'ja' else 'English'} print, "
                              "but the text says otherwise: one of them is wrong."))

    for c, pat, meaning in _RISKS:
        m = re.search(pat, low)
        if m:
            flags.append(Flag(c, m.group(0).strip(), meaning))
    if code and code["kind"] == "cert_number":
        flags.append(Flag("cert_as_code", code["raw"], "A grading cert number where a set code should be."))
    if len(set(rarities)) > 1:
        flags.append(Flag("rarity_ambiguous", " / ".join(rarities),
                          "Several rarities named: listing may cover multiple versions (variation listing)."))

    sig = ListingSignals(
        title=title_n, seller_set_code=seller_code, seller_rarity=rarities[0] if rarities else None,
        seller_edition=edition, slab_cert=cert, is_multi_card=is_lot and qty > 1,
        card_language=None,   # text is not proof of the card's language
    )
    return ParsedListing(title_n, body_n, sig, rarities, edition, grade_co, grade, cond, qty, is_lot,
                         is_collection, lang_hint, claims, flags)
