"""Read sold prices out of pages you save from marketplaces (Cmd+S in the browser), so no site is scraped.

Supported: eBay sold/completed search results and sold item pages, 130point results, Yahoo! Auctions closed results
(落札相場 / closedsearch), Mercari sold (売り切れ) searches, and Safari .webarchive copies of any of them.

What a row becomes:
* a plain sold price -> MARKETPLACE_MARKED_SOLD (the site says it sold; settlement is not proven)
* eBay "Best Offer accepted" with the list price struck through -> SELLER_CLAIM: the real amount is hidden, so the
  number is only an upper bound and never a comparable
* 130point's revealed accepted offer -> ACCEPTED_OFFER at the real amount, tied to the same eBay item so the two
  records of one sale count once
Lots and graded cards are flagged from the title. Seller names and profiles are removed before the page is stored,
and no username is ever kept.
"""
from __future__ import annotations

import hashlib
import plistlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from .listing_parser import parse_listing_text

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
DROP = {"script", "style", "noscript", "template", "svg"}
# Anything that identifies a person: removed before a page is stored or read.
PRIVATE = re.compile(r"seller|username|user-?name|store-?info|member|feedback|profile|bidder|buyer", re.I)
SYNTHETIC_MARK = "CARDMAN-SYNTHETIC-FIXTURE"


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or {}), [], parent

    @property
    def cls(self) -> str:
        return self.attrs.get("class") or ""

    def has(self, token: str) -> bool:
        return token in self.cls.split() or token in self.cls

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return " ".join(x for x in out if x).strip()

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find(self, pred):
        return next((n for n in self.iter() if pred(n)), None)

    def find_all(self, pred):
        return [n for n in self.iter() if pred(n)]


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag not in VOID:
                self.skip += 1
            return
        if tag in DROP:
            self.skip = 1
            return
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        if not self.skip and tag not in DROP:
            self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.cur.children.append(re.sub(r"\s+", " ", data))


def parse_html(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


def _is_private(n: Node) -> bool:
    keys = " ".join(str(n.attrs.get(k) or "") for k in ("class", "id", "data-testid", "aria-label", "itemprop"))
    href = n.attrs.get("href") or ""
    return bool(PRIVATE.search(keys)) or "/usr/" in href or "/sch/i.html?_ssn=" in href or "/user/" in href \
        or "/profile/" in href or "seller" in href


# Text that names a person even without a telling class: "Seller: name", "出品者 name", eBay's "name (1,234) 99.8%".
PRIVATE_TEXT = re.compile(r"^\s*(?:seller|sold by|bidder|buyer|出品者|販売者|落札者)\s*[:：]?|\S+\s*\(\s*[\d,]+\s*\)\s*\d{2,3}(?:\.\d+)?\s*%", re.I)


def strip_people(root: Node) -> Node:
    """Remove every element that names or links a person (seller, bidder, profile), and text that does."""
    def walk(n: Node):
        n.children = [c for c in n.children if (not PRIVATE_TEXT.search(c) if isinstance(c, str) else not _is_private(c))]
        for c in n.children:
            if isinstance(c, Node):
                walk(c)
    walk(root)
    return root


def serialize(n: Node) -> str:
    """HTML back out of the tree (people removed, scripts gone): this is what gets stored."""
    if n.tag == "#root":
        return "".join(c if isinstance(c, str) else serialize(c) for c in n.children)
    import html as h
    attrs = "".join(f' {k}="{h.escape(str(v), quote=True)}"' for k, v in n.attrs.items() if v is not None and not k.startswith("on"))
    if n.tag in VOID:
        return f"<{n.tag}{attrs}>"
    inner = "".join(h.escape(c, quote=False) if isinstance(c, str) else serialize(c) for c in n.children)
    return f"<{n.tag}{attrs}>{inner}</{n.tag}>"


def from_webarchive(data: bytes) -> str:
    d = plistlib.loads(data)
    res = d["WebMainResource"]
    enc = res.get("WebResourceTextEncodingName") or "utf-8"
    return res["WebResourceData"].decode(enc, errors="replace")


# -- money and dates --------------------------------------------------------------------------------------------------

_CUR = [("US $", "USD"), ("US$", "USD"), ("C $", "CAD"), ("AU $", "AUD"), ("NT$", "TWD"), ("HK$", "HKD"), ("£", "GBP"), ("€", "EUR"),
        ("EUR", "EUR"), ("¥", "JPY"), ("￥", "JPY"), ("円", "JPY"), ("$", "USD")]
_AMOUNT = re.compile(r"(\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.(\d{1,2}))?")


def money(text: str, default: str | None = None) -> tuple[Decimal, str] | None:
    t = unicodedata.normalize("NFKC", text or "").strip()
    if re.search(r"\bto\b|〜|~", t):           # "$10.00 to $20.00": a range is not a price
        return None
    cur = next((c for sym, c in _CUR if sym in t), default)
    m = _AMOUNT.search(t)
    if not m or not cur:
        return None
    try:
        return Decimal(m.group(1).replace(",", "").replace("，", "") + ("." + m.group(2) if m.group(2) else "")), cur
    except InvalidOperation:
        return None


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def sold_date(text: str, captured: datetime) -> datetime | None:
    t = unicodedata.normalize("NFKC", text or "")
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}),? (\d{4})", t)               # Nov 3, 2026
    if m and m.group(1).lower() in _MONTHS:
        return datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc)
    m = re.search(r"(\d{1,2}) ([A-Z][a-z]{2})[a-z]* (\d{4})", t)                    # 3 Nov 2026
    if m and m.group(2).lower() in _MONTHS:
        return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", t)                         # 2026/11/03
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    m = re.search(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", t)                    # 11/03 21:30 (Yahoo: no year, JST)
    if m:
        jst = timezone(timedelta(hours=9))
        y = captured.astimezone(jst).year
        d = datetime(y, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), tzinfo=jst)
        if d > captured + timedelta(days=1):         # a December result saved in January
            d = d.replace(year=y - 1)
        return d.astimezone(timezone.utc)
    return None


# -- rows -------------------------------------------------------------------------------------------------------------

@dataclass
class SoldRow:
    site: str
    item_id: str
    title: str
    price: Decimal
    currency: str
    kind: str                   # "sold" | "accepted_offer" | "best_offer_hidden"
    sold_at: datetime | None
    url: str
    shipping: Decimal | None = None
    date_is_capture: bool = False
    notes: list[str] = field(default_factory=list)
    lineage: str | None = None   # "ebay:<item id>" when this row is a copy of an eBay sale

    def flags(self) -> dict:
        p = parse_listing_text(self.title)
        return {"is_lot": p.is_lot, "lot_size": p.lot_size, "grade_company": p.grade_company, "grade": p.grade, "condition": p.condition}


def _ebay_item_id(href: str) -> str | None:
    m = re.search(r"/itm/(?:[^/?#]+/)?(\d{9,15})", href or "")
    return m.group(1) if m else None


def _first(n: Node, *tokens) -> Node | None:
    return n.find(lambda x: any(x.has(t) for t in tokens))


def parse_ebay_results(root: Node, captured: datetime) -> list[SoldRow]:
    rows = []
    items = root.find_all(lambda n: n.tag == "li" and (n.has("s-item") or n.has("s-card")))
    for it in items:
        link = it.find(lambda n: n.tag == "a" and _ebay_item_id(n.attrs.get("href")))
        if not link:
            continue
        iid = _ebay_item_id(link.attrs["href"])
        title_n = _first(it, "s-item__title", "s-card__title")
        title = re.sub(r"^New Listing\s*", "", (title_n.text() if title_n else link.text()) or "").strip()
        if not title or title.lower().startswith("shop on ebay"):
            continue
        text = it.text()
        when = sold_date(re.search(r"Sold\s+[^|·]{4,25}", text).group(0) if re.search(r"Sold\s+[^|·]{4,25}", text) else "", captured)
        if not when:
            continue                                 # not a sold row (an active listing or an ad)
        price_n = _first(it, "s-item__price", "s-card__price")
        struck = it.find(lambda n: n.has("STRIKETHROUGH") or n.tag in ("s", "del") or "line-through" in (n.attrs.get("style") or ""))
        best_offer = bool(re.search(r"best offer accepted", text, re.I))
        shown = money(price_n.text() if price_n else "")
        if not shown:
            continue
        ship = None
        sm = re.search(r"\+\s*((?:US\s*)?\$[\d,.]+|£[\d,.]+|€[\d,.]+)\s*(?:shipping|delivery|postage)", text, re.I)
        if sm:
            got = money(sm.group(1))
            ship = got[0] if got and got[1] == shown[1] else None
        elif re.search(r"free (?:shipping|delivery|postage)", text, re.I):
            ship = Decimal(0)
        url = f"https://www.ebay.com/itm/{iid}"
        if best_offer or struck:
            rows.append(SoldRow("ebay", iid, title, shown[0], shown[1], "best_offer_hidden", when, url, ship, lineage=f"ebay:{iid}",
                                notes=["Best Offer accepted: the real price is hidden; the shown price is only an upper bound"]))
        else:
            rows.append(SoldRow("ebay", iid, title, shown[0], shown[1], "sold", when, url, ship, lineage=f"ebay:{iid}"))
    return rows


def parse_ebay_item(root: Node, captured: datetime) -> list[SoldRow]:
    text = root.text()
    if not re.search(r"this listing sold|sold on|ended:.*sold|winning bid", text, re.I):
        return []
    canon = root.find(lambda n: n.tag == "link" and n.attrs.get("rel") == "canonical")
    iid = _ebay_item_id(canon.attrs.get("href", "") if canon else "") or \
        (re.search(r"(?:item number|商品番号)[:：\s]*(\d{9,15})", text, re.I) or [None, None])[1]
    t = _first(root, "x-item-title__mainTitle", "x-item-title")
    title = t.text() if t else (root.find(lambda n: n.tag == "h1") or Node("x")).text()
    pn = _first(root, "x-price-primary", "x-bin-price", "vi-price")
    shown = money(pn.text() if pn else "")
    when = sold_date((re.search(r"(?:sold on|ended)[:\s]+([^|]{6,30})", text, re.I) or [None, ""])[1] or "", captured)
    if not (iid and title and shown):
        return []
    hidden = bool(re.search(r"best offer accepted", text, re.I))
    return [SoldRow("ebay", iid, title, shown[0], shown[1], "best_offer_hidden" if hidden else "sold", when or captured,
                    f"https://www.ebay.com/itm/{iid}", date_is_capture=when is None, lineage=f"ebay:{iid}",
                    notes=(["Best Offer accepted: the real price is hidden"] if hidden else []) +
                          (["sale date not on the page; the date you saved it is used"] if when is None else []))]


def parse_130point(root: Node, captured: datetime) -> list[SoldRow]:
    """Each result links its eBay item; the price shown is the real sale price, including revealed Best Offers."""
    rows = []
    for link in root.find_all(lambda n: n.tag == "a" and _ebay_item_id(n.attrs.get("href"))):
        row = link
        while row.parent is not None and row.parent.tag != "#root" and row.tag not in ("tr", "li") and not row.has("sold_data"):
            row = row.parent
        text = row.text()
        iid = _ebay_item_id(link.attrs["href"])
        if any(r.item_id == iid for r in rows):
            continue
        sale = re.search(r"(?:Sale|Sold)\s*Price[:\s]*([^\n|]*?\d[\d,.]*)", text, re.I) or re.search(r"((?:US\s*)?\$\s?[\d,]+(?:\.\d{2})?)", text)
        shown = money(sale.group(1)) if sale else None
        when = sold_date(text, captured)
        title = link.text() or text[:120]
        if not (shown and when and title):
            continue
        accepted = bool(re.search(r"best offer accepted|accepted offer", text, re.I))
        rows.append(SoldRow("130point", iid, title, shown[0], shown[1], "accepted_offer" if accepted else "sold", when,
                            f"https://www.ebay.com/itm/{iid}", lineage=f"ebay:{iid}",
                            notes=(["Best Offer amount revealed by 130point"] if accepted else [])))
    return rows


def parse_yahoo_closed(root: Node, captured: datetime) -> list[SoldRow]:
    rows = []
    for it in root.find_all(lambda n: n.tag == "li" and n.has("Product")):
        a = it.find(lambda n: n.tag == "a" and re.search(r"auctions\.yahoo\.co\.jp/jp/auction/([a-z]?\d+)", n.attrs.get("href") or ""))
        if not a:
            continue
        aid = re.search(r"/auction/([a-z]?\d+)", a.attrs["href"]).group(1)
        tn = _first(it, "Product__titleLink", "Product__title")
        title = (tn.text() if tn else a.text()) or (a.attrs.get("data-auction-title") or "")
        pn = _first(it, "Product__priceValue")
        shown = money(pn.text() if pn else "", default="JPY")
        tm = _first(it, "Product__time")
        when = sold_date(tm.text() if tm else "", captured)
        if not (title and shown):
            continue
        bids = _first(it, "Product__bid")
        rows.append(SoldRow("yahoo_auctions_jp", aid, title.strip(), shown[0], shown[1], "sold", when or captured,
                            f"https://page.auctions.yahoo.co.jp/jp/auction/{aid}", date_is_capture=when is None,
                            notes=[f"{bids.text()} bids" if bids else "bids not shown"] + (["end date not on the page"] if when is None else [])))
    return rows


def yahoo_next_data(html: str, captured: datetime) -> list[SoldRow]:
    """Yahoo! Auctions now renders results from a JSON block (__NEXT_DATA__) rather than Product__ list items.
    Only the sale fields are read; the seller object in each item is never touched or stored."""
    import json
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        items = data["props"]["pageProps"]["initialState"]["search"]["items"]["listing"]["items"]
    except (ValueError, KeyError, TypeError):
        return []
    rows = []
    for it in items:
        aid, title, price = it.get("auctionId"), (it.get("title") or "").strip(), it.get("price")
        if not (aid and title and isinstance(price, (int, float)) and price > 0) or it.get("isNoPrice"):
            continue
        try:
            when = datetime.fromisoformat(it["endTime"]).astimezone(timezone.utc) if it.get("endTime") else None
        except ValueError:
            when = None
        bids = it.get("bidCount")
        fixed = bool(it.get("isFixedPrice") or it.get("isFleamarketItem"))
        notes = ["fixed price (flea market) sale" if fixed else f"{bids} bids" if bids is not None else "bids not shown"]
        if when is None:
            notes.append("end date not on the page")
        rows.append(SoldRow("yahoo_auctions_jp", str(aid), title, Decimal(str(price)), "JPY", "sold", when or captured,
                            f"https://page.auctions.yahoo.co.jp/jp/auction/{aid}",
                            shipping=Decimal(0) if it.get("isFreeShipping") else None, date_is_capture=when is None, notes=notes))
    return rows


def parse_mercari_sold(root: Node, captured: datetime) -> list[SoldRow]:
    rows = []
    for a in root.find_all(lambda n: n.tag == "a" and re.search(r"/item/(m\d{6,})", n.attrs.get("href") or "")):
        mid = re.search(r"/item/(m\d{6,})", a.attrs["href"]).group(1)
        if any(r.item_id == mid for r in rows):
            continue
        text = a.text()
        label = " ".join(str(n.attrs.get("aria-label") or "") for n in [a] + a.find_all(lambda n: True))
        if not re.search(r"売り切れ|SOLD", f"{text} {label}", re.I):
            continue
        nm = a.find(lambda n: n.attrs.get("data-testid") == "thumbnail-item-name") or a.find(lambda n: n.tag == "img" and n.attrs.get("alt"))
        title = (nm.text() if nm and nm.tag != "img" else (nm.attrs.get("alt") if nm else "")) or ""
        title = re.sub(r"のサムネイル$", "", title).strip()
        pn = a.find(lambda n: "price" in n.cls.lower() or n.attrs.get("data-testid") == "price")
        shown = money(pn.text() if pn else text, default="JPY")
        if not (title and shown):
            continue
        rows.append(SoldRow("mercari_jp", mid, title, shown[0], shown[1], "sold", captured, f"https://jp.mercari.com/item/{mid}",
                            date_is_capture=True, notes=["Mercari search shows no sale date; the date you saved the page is used, so the sale was on or before it"]))
    return rows


def detect(html: str, name: str = "") -> str | None:
    low = html[:400000].lower()
    if "130point" in low:
        return "130point"
    if "auctions.yahoo.co.jp" in low and ("closedsearch" in low or "落札" in html[:400000]):
        return "yahoo"
    if "mercari" in low and "/item/m" in low:
        return "mercari"
    if "ebay." in low and ("s-item" in low or "s-card" in low):
        return "ebay_results"
    if "ebay." in low and ("x-item-title" in low or "/itm/" in low):
        return "ebay_item"
    return None


PARSERS = {"ebay_results": parse_ebay_results, "ebay_item": parse_ebay_item, "130point": parse_130point,
           "yahoo": parse_yahoo_closed, "mercari": parse_mercari_sold}


@dataclass
class PageResult:
    site: str | None
    rows: list[SoldRow]
    stored_html: str           # people removed: the only form of the page that is kept
    synthetic: bool
    sha256: str


def read_page(name: str, data: bytes, captured: datetime) -> PageResult:
    html = from_webarchive(data) if name.lower().endswith(".webarchive") else data.decode("utf-8", errors="replace")
    site = detect(html, name)
    root = strip_people(parse_html(html))
    rows = PARSERS[site](root, captured) if site else []
    if site == "yahoo" and not rows:
        rows = yahoo_next_data(html, captured)
    return PageResult(site, rows, serialize(root), SYNTHETIC_MARK in html, hashlib.sha256(data).hexdigest())
