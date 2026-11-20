"""Read what a seller's listing text claims (JP / EN / zh-TW), without believing any of it.

Everything returned here is a seller claim. It can narrow what to look at and expose contradictions;
it never counts as verified evidence for the resolver.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .identity import parse_set_code

_NOT_LATIN = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
_KANA_WORD = r"(?<![ァ-ヶー]){}(?![ァ-ヶー])"

# Longest / most specific first: a matched span is consumed, so "Ultra Parallel Rare" is not also "Ultra Rare".
RARITY_TERMS: list[tuple[str, str]] = [
    ("Quarter Century Secret Rare", r"quarter century(?: secret)?(?: rare)?|qcsr|qcse?r?\b|クォーターセンチュリー\S*|クオーターセンチュリー\S*|クオシク|クォーターシークレット|25th\s*シク"),
    ("Prismatic Secret Rare", r"prismatic secret(?: rare)?|\bpscr\b|プリズマティックシークレット(?:レア)?|プリシク"),
    ("Platinum Secret Rare", r"platinum secret(?: rare)?|\bplscr\b|プラチナシークレット(?:レア)?|プラシク"),
    ("Extra Secret Rare", r"extra secret(?: rare)?|エクストラシークレット(?:レア)?|エクシク"),
    ("20th Secret Rare", r"20th secret(?: rare)?|20thシークレット(?:レア)?|20thシク"),
    ("Secret Parallel Rare", r"secret parallel(?: rare)?|シークレットパラレル(?:レア)?|シクパラ"),
    ("Ultra Parallel Rare", r"ultra parallel(?: rare)?|parallel ultra(?: rare)?|ウルトラパラレル(?:レア)?|ウルパラ"),
    ("Super Parallel Rare", r"super parallel(?: rare)?|スーパーパラレル(?:レア)?|スーパラ"),
    ("Normal Parallel Rare", r"normal parallel(?: rare)?|ノーマルパラレル(?:レア)?|ノーパラ"),
    ("Starlight Rare", r"starlight(?: rare)?|\bstr\b|スターライト(?:レア)?|スタレ"),
    ("Ultimate Rare", r"ultimate(?: rare)?|アルティメット(?:レア)?|レリーフ|\brelief\b|凸版|浮雕"),
    ("Ghost Rare", r"ghost(?: rare)?|ゴースト(?:レア)?|\bgr\b"),
    ("Holographic Rare", r"holographic(?: rare)?|ホログラフィック(?:レア)?|\bhgr\b"),
    ("Collector's Rare", r"collector'?s rare|\bcr\b|コレクターズ(?:レア)?|コレレア"),
    ("Gold Secret Rare", r"gold secret(?: rare)?|ゴールドシークレット(?:レア)?"),
    ("Premium Gold Rare", r"premium gold(?: rare)?|プレミアムゴールド(?:レア)?"),
    ("Gold Rare", r"gold rare|ゴールドレア"),
    ("Millennium Rare", r"millennium(?: rare)?|ミレニアム(?:レア)?"),
    ("Mosaic Rare", r"mosaic(?: rare)?"),
    ("Starfoil Rare", r"starfoil(?: rare)?"),
    ("Shatterfoil Rare", r"shatterfoil(?: rare)?"),
    ("Secret Rare", r"secret(?: rare)?|\bscr\b|シークレット(?:レア)?|\bse\b|シク"),
    ("Ultra Rare", r"ultra(?: rare)?|\bur\b|ウルトラ(?:レア)?|ウル|金亮"),
    ("Super Rare", r"super(?: rare)?|\bsr\b|スーパー(?:レア)?|亮面"),
    ("Short Print", r"short print|\bsp\b"),
    ("Common", r"\bcommon\b|ノーマル|ノーレア"),
    ("Rare", r"(?<![A-Za-z])rare(?![A-Za-z])|(?<![ァ-ヶー])レア(?![ァ-ヶー])|銀字"),
]
_RARITY_RE = [(name, re.compile(pat, re.I)) for name, pat in RARITY_TERMS]

_GRADE_RE = re.compile(r"(?<![A-Za-z])(PSA|BGS|CGC|ARS|SGC|TAG|GMA|BECKETT|AGS)[\s\-:]*(?:GEM[\s\-]*(?:MINT|MT|-?MT)?\s*|MINT\s*|PRISTINE\s*|鑑定\s*)?(\d{1,2}(?:\.\d)?)(?![\d])", re.I)
_CERT_RE = re.compile(r"(?:cert|certificate|証明書|鑑定番号|編號)[\s#:：No\.]*(\d{7,10})", re.I)
_LOT_SIZE_RE = re.compile(r"(\d{1,5})\s*(?:枚|張|cards?\b|pcs\b|ct\b)", re.I)

COUNTERFEIT_CUES = ("proxy", "プロキシ", "orica", "オリカ", "オリジナルカード", "replica", "レプリカ", "fake", "偽物", "コピー品",
                    "コピーカード", "複製", "海賊版", "盜版", "盗版", "仿製", "custom card", "カスタムカード", "fan made", "fanmade",
                    "非公式", "unofficial", "not authentic", "novelty", "ノベルティカード", "同人")
NOT_A_CARD_CUES = ("sleeve", "スリーブ", "playmat", "プレイマット", "デッキケース", "deck box", "deck case", "ストレージ", "空箱", "empty box",
                   "empty tin", "poster", "ポスター", "フィギュア", "figure", "攻略本", "guide book", "ガイドブック", "カードケース", "卡套", "卡墊")
IGNORANCE_CUES = ("詳細不明", "詳しくありません", "詳しくない", "よくわかりません", "よく分かりません", "わかりません", "分かりません", "素人", "知識がな",
                  "価値がわから", "価値が分から", "未確認", "未検品", "未検索", "ノーチェック", "親の", "祖父", "実家", "don't know much", "dont know much",
                  "no idea", "not sure what", "unsearched", "unchecked", "found in attic", "grandfather", "my dad", "estate", "不懂", "不清楚", "不確定", "不太懂", "阿公")
LOT_CUES = ("まとめ", "大量", "セット売り", "引退", "コレクション", "バインダー", "ファイル", "ひとまとめ", "bulk", "binder", "collection", "bundle",
            " lot", "lot of", "整批", "一批", "卡冊", "出清")
REPRINT_CUES = ("reprint", "再録", "再販", "復刻", "25th", "25周年", "anniversary", "レプリカ")
VINTAGE_CUES = ("初期", "2期", "二期", "vintage", "1999", "2002", "旧枠", "絕版", "初代")
CONDITION_TERMS = (("NM", r"\bnm\b|near mint|極美品|完美品"), ("EX", r"\bex\b|excellent|美品"), ("LP", r"\blp\b|lightly played"),
                   ("MP", r"\bmp\b|moderately played|傷あり|キズあり|初期傷|白欠け"), ("HP", r"\bhp\b|heavily played|damaged|ジャンク|折れ"))
LANGUAGE_TERMS = (("asian_en", r"asian[\s-]*english|アジア版|亞英|亚英|\bae\b"), ("ko", r"korean|韓国|韓版|韩版"),
                  ("zh", r"chinese|中文版|繁中"), ("ja", r"japanese|日本語|日版|\bocg\b"), ("en", r"english|英語|英文版|美版|\btcg\b"))


@dataclass(frozen=True)
class ParsedListing:
    text: str
    set_codes: tuple[str, ...] = ()
    rarities: tuple[str, ...] = ()
    editions: tuple[str, ...] = ()
    grade_company: str | None = None
    grade: str | None = None
    cert: str | None = None
    condition: str | None = None
    language: str | None = None
    is_lot: bool = False
    lot_size: int | None = None
    counterfeit_cues: tuple[str, ...] = ()
    not_a_card_cues: tuple[str, ...] = ()
    ignorance_cues: tuple[str, ...] = ()
    reprint_cues: tuple[str, ...] = ()
    vintage_cues: tuple[str, ...] = ()


def _cues(low: str, cues: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(c.strip() for c in cues if c in low)


def parse_listing_text(text: str) -> ParsedListing:
    norm = unicodedata.normalize("NFKC", text or "")
    low = f" {norm.lower()} "

    g = _GRADE_RE.search(norm)
    grade_co, grade = (g.group(1).upper().replace("BECKETT", "BGS"), g.group(2)) if g else (None, None)
    cert = (m.group(1) if (m := _CERT_RE.search(norm)) else None)

    no_grade = _GRADE_RE.sub(" ", norm)
    codes = []
    for tok in re.findall(r"(?:RD/)?[A-Za-z0-9]{2,5}-[A-Za-z0-9]{2,7}", no_grade):
        c = parse_set_code(tok)
        if c and c.get("kind") == "set_code" and c["code"] not in codes:
            codes.append(c["code"])

    rest = no_grade
    for c in codes:
        rest = re.sub(re.escape(c), " ", rest, flags=re.I)
    rarities = []
    for name, rx in _RARITY_RE:
        if rx.search(rest):
            rest = rx.sub(" ", rest)
            rarities.append(name)

    editions = []
    if re.search(r"\b1st\b|1st ?ed|first edition|初版", low):
        editions.append("1st")
    if re.search(r"\bunlimited\b|\bunli\b|無限制", low):
        editions.append("Unlimited")
    if re.search(r"limited edition|\ble\b", low):
        editions.append("Limited")

    condition = next((n for n, p in CONDITION_TERMS if re.search(p, low)), None)
    language = next((n for n, p in LANGUAGE_TERMS if re.search(p, low)), None)
    # Sizes of 1-3 are usually "x3 same card", or noise; a lot needs a cue or a real count.
    sizes = [int(x) for x in _LOT_SIZE_RE.findall(norm)]
    lot_size = max(sizes) if sizes else None
    is_lot = bool(_cues(low, LOT_CUES)) or (lot_size is not None and lot_size >= 2)
    if len(codes) >= 3:
        is_lot = True
    return ParsedListing(
        text=text or "", set_codes=tuple(codes), rarities=tuple(rarities), editions=tuple(editions),
        grade_company=grade_co, grade=grade, cert=cert, condition=condition, language=language, is_lot=is_lot,
        lot_size=lot_size, counterfeit_cues=_cues(low, COUNTERFEIT_CUES), not_a_card_cues=_cues(low, NOT_A_CARD_CUES),
        ignorance_cues=_cues(low, IGNORANCE_CUES), reprint_cues=_cues(low, REPRINT_CUES),
        vintage_cues=_cues(low, VINTAGE_CUES))
