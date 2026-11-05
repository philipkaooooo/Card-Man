"""Konami official card database: per-card product list (CLAUDE.md section 1).

Tested live 2026-11-05 on card_search.action?ope=2&cid={konami_id}&request_locale=ja: the
page lists every Japanese release of the card with release date, print code (blank for vintage
products), product name, Konami product id (pid) and rarity label. This is the primary JP OCG
source; it lists no editions. One page per card, fetched on demand and cached; never crawled.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

KONAMI_VERSION = "konami-db-0.1.0"
CARD_URL = "https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid={cid}&request_locale={loc}"


@dataclass(frozen=True)
class KonamiRelease:
    date: str | None
    code: str | None
    product: str
    pid: str | None
    rarity_short: str | None
    rarity_label: str | None


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_card_page(page: str) -> list[KonamiRelease]:
    i = page.find('id="update_list"')
    if i < 0:
        raise ValueError("no update_list block: page layout changed or card not found")
    out = []
    for row in re.split(r'<div class="t_row', page[i:])[1:]:
        if 'class="time"' not in row:
            break   # past the release list (related-card rows follow)
        date = re.search(r'class="time">(.*?)</div>', row, re.S)
        code = re.search(r'class="card_number">(.*?)</div>', row, re.S)
        prod = re.search(r'class="pack_name[^"]*"\s*>(.*?)</div>', row, re.S)
        pid = re.search(r'pid=(\d+)', row)
        rar = re.search(r'class="lr_icon[^"]*"[^>]*>\s*<p>(.*?)</p>\s*<span[^>]*>(.*?)</span>', row, re.S)
        out.append(KonamiRelease(
            date=_text(date.group(1)) or None if date else None,
            code=(_text(code.group(1)) or None) if code else None,
            product=_text(prod.group(1)) if prod else "",
            pid=pid.group(1) if pid else None,
            rarity_short=_text(rar.group(1)) if rar else None,
            rarity_label=_text(rar.group(2)) if rar else None,
        ))
    return out
