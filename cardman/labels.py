"""Official names. A printed code is not always one card.

Checked against TCGplayer product details on 2026-11-21:

* 21792 is Blue-Eyes White Dragon in The Legend of Blue Eyes White Dragon.
  The card is printed LOB-001. Sellers list it as 1st Edition or Unlimited.
* 476160 is the same monster in Legend of Blue Eyes White Dragon (Worldwide English).
  The card is printed LOB-EN001. Unlimited only.
* 486054 is the same monster in Legend of Blue Eyes White Dragon (25th Anniversary Edition).
  The card is also printed LOB-EN001. Unlimited only.

LOB-001 and LOB-EN001 are different cards. The two LOB-EN001 cards are different
sets that share a number. The set name separates them. The number does not.
`code_key` still folds both numbers together to find a date. This module does not.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

@dataclass(frozen=True)
class ProductName:
    product_id: int
    card: str
    set_name: str
    set_code: str
    number: str
    variants: tuple[str, ...]
    language: str


# Order is the order a search prints.
_PRODUCTS = (
    ProductName(21792, "Blue-Eyes White Dragon", "The Legend of Blue Eyes White Dragon",
                "LOB", "LOB-001", ("1st Edition", "Unlimited"), "English"),
    ProductName(476160, "Blue-Eyes White Dragon", "Legend of Blue Eyes White Dragon (Worldwide English)",
                "LOB-EN", "LOB-EN001", ("Unlimited",), "English"),
    ProductName(486054, "Blue-Eyes White Dragon", "Legend of Blue Eyes White Dragon (25th Anniversary Edition)",
                "LOB-EN", "LOB-EN001", ("Unlimited",), "English"),
)
_BY_ID = {p.product_id: p for p in _PRODUCTS}
_BY_NUMBER: dict[str, tuple[ProductName, ...]] = {}
for _p in _PRODUCTS:
    _BY_NUMBER.setdefault(_p.number, ())
    _BY_NUMBER[_p.number] = _BY_NUMBER[_p.number] + (_p,)

# Unsiffixed codes that also exist as Asian-English. Not given an English product here.
_AE_SAME_CHARACTERS = frozenset({"LOB-001", "SDK-001", "LOB-005", "SDY-006", "MFC-000"})


def by_id(product_id: int) -> ProductName | None:
    return _BY_ID.get(int(product_id))


def by_number(code: str) -> tuple[ProductName, ...]:
    return _BY_NUMBER.get(code.upper(), ())


def stated(card: str | None, set_name: str | None, number: str | None,
           variant: str = "", condition: str = "", language: str = "") -> str:
    """One order everywhere: card, set, printed number, then the stated variant, condition and language.

    The variant and condition are what the seller listed. They are not a check.
    A shared number with no set name is not resolved to a product.
    """
    parts = [p for p in (card, set_name, number, variant, condition, language) if p]
    line = " · ".join(parts)
    if number and not set_name and len(by_number(number)) > 1:
        line = (line + " · " if line else "") + "this number is on more than one set; the set name is required"
    return line


def sku_line(product_id: int | None, variant: str, condition: str, language: str) -> str:
    """Official card and set when this product is one we have named. Otherwise the id, unresolved."""
    p = by_id(product_id) if product_id is not None else None
    if p is None:
        return stated(f"product {product_id}", "", "", variant, condition, language)
    return stated(p.card, p.set_name, p.number, variant, condition, language)


def show(key: str | None) -> str:
    """The stored key, printed as the official line. Unknown keys are not renamed."""
    if not key:
        return "-"
    kind, _, rest = key.partition(":")
    parts = rest.split("|")
    if len(parts) != 4:
        return key
    if kind == "tcg" and parts[0].isdigit():
        return sku_line(int(parts[0]), parts[1], parts[2], parts[3])
    if kind == "code":
        return f"{parts[0]} · {parts[1]} · {parts[2]} · {parts[3]} · no product joined"
    return key


def first_stamp(code: str) -> str:
    """Why a 1st stamp on this code is refused, in official names when we have them."""
    rows = by_number(code)
    if not rows:
        return f"{code} is a worldwide English code. That run is Unlimited, so a 1st stamp contradicts it."
    listed = "; ".join(f"{r.set_name} (product {r.product_id})" for r in rows)
    text = f"{code} is printed on {listed}. Those runs are Unlimited, so a 1st stamp contradicts the card."
    if code == "LOB-EN001":
        text += " LOB-001 is The Legend of Blue Eyes White Dragon, product 21792, and it is not this code."
    return text


def wrong_product(code: str, product_id: int) -> str:
    """The code and the product id are not the same card."""
    named = by_id(product_id)
    rows = by_number(code)
    tail = ""
    if named:
        tail += f" Product {product_id} is {named.card}, {named.set_name}, printed {named.number}."
    if rows:
        tail += " " + code + " is " + " or ".join(
            f"{r.set_name} (product {r.product_id})" for r in rows) + "."
    return f"{code} is not product {product_id}.{tail}"


def explain(query: str) -> str:
    """One search. A code, or a product id. Says what the card is and what people mix it up with."""
    text = query.strip()
    if text.isdigit():
        p = by_id(int(text))
        if p is None:
            return f"Product {text} has no official name on file."
        return "\n".join(_product_lines(p))
    code = text.upper()
    rows = by_number(code)
    if not rows:
        if code in _AE_SAME_CHARACTERS:
            return (f"{code} is not in the official-name table. The same characters are also an Asian-English code, "
                    "so the code alone is not joined to an English product.")
        return f"{code} has no official name on file. It is not given a set name from a different card."
    lines = []
    if len(rows) == 1:
        p = rows[0]
        lines.append(f"{code} is one English TCG card.")
        lines.extend(_product_lines(p))
    else:
        lines.append(f"{code} is {len(rows)} cards, not one. The set name separates them. The printed number does not.")
        for p in rows:
            lines.extend(_product_lines(p))
    if code == "LOB-EN001":
        lines.append("LOB-001 is not either of these. LOB-001 is The Legend of Blue Eyes White Dragon, product 21792.")
    if code == "LOB-001":
        lines.append("It is not LOB-EN001. LOB-EN001 is Worldwide English (product 476160) or the 25th Anniversary "
                     "(product 486054).")
        lines.append("The same characters LOB-001 are also used by an Asian-English card. That card is not product 21792. "
                     "A seller writing LOB-001 does not decide which one it is.")
    return "\n".join(lines)


def _variants(p: ProductName) -> str:
    if p.variants == ("Unlimited",):
        return "Unlimited only"
    return " or ".join(p.variants)


def explain_details(doc: dict) -> str:
    """A product payload's own name. A known shared number is listed again so it cannot look unique."""
    if not isinstance(doc, dict) or doc.get("productId") is None:
        return "No product in that response."
    pid = int(doc["productId"])
    if by_id(pid):
        return explain(str(pid))
    attrs = doc.get("customAttributes") or {}
    number = str(attrs["number"]) if attrs.get("number") else ""
    variants = sorted({str(s.get("variant")) for s in doc.get("skus") or [] if isinstance(s, dict) and s.get("variant")})
    languages = sorted({str(s.get("language")) for s in doc.get("skus") or [] if isinstance(s, dict) and s.get("language")})
    head = stated(str(doc.get("productName") or ""), str(doc.get("setName") or ""), number)
    tail = " · ".join(p for p in (f"product {pid}", " or ".join(variants), ", ".join(languages)) if p)
    lines = [f"{head} · {tail}" if head else tail]
    if number and len(by_number(number)) > 1:
        lines.append(explain(number))
    return "\n".join(lines)


def _product_lines(p: ProductName) -> list[str]:
    return [f"{p.card} · {p.set_name} · {p.number} · product {p.product_id} · {_variants(p)} · {p.language}"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cardman.labels")
    ap.add_argument("query", nargs="+", help="a printed code or a product id")
    args = ap.parse_args(argv)
    for i, query in enumerate(args.query):
        if i:
            print()
        if query.strip().isdigit() and by_id(int(query.strip())) is None:
            try:
                from .tcgplayer import fetch_details
                print(explain_details(fetch_details(int(query.strip()))))
            except Exception as exc:
                print(f"Product {query.strip()} has no official name on file. Lookup failed: {type(exc).__name__}.")
            continue
        print(explain(query))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
