# language: Python 3.11+, file: cardman/spread.py
"""Active asking vs sold-net spread scanner.
Uses the parsers from market_live.py.
Flags only clean spreads after fees.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from datetime import datetime, timezone

from .market_live import ebay_sold, yahoo_jp_closed, net, LiveHit, Session, EBAY_SOLD, YAHOO_CLOSED, _parse_price
from selectolax.parser import HTMLParser
import re
import hashlib

def ebay_active(query: str, limit: int = 40) -> list[LiveHit]:
    sess = Session()
    params = {
        "_nkw": query,
        "_ipg": "60",
        "rt": "nc",
        "LH_BIN": "1",          # Buy It Now only for cleaner asks
    }
    hits = []
    try:
        r = sess.get(EBAY_SOLD, params=params)
        if r.status_code != 200:
            return hits
        tree = HTMLParser(r.text)
        for item in tree.css("li.s-item"):
            title_node = item.css_first(".s-item__title")
            price_node = item.css_first(".s-item__price")
            link_node = item.css_first("a.s-item__link")
            if not (title_node and price_node and link_node):
                continue
            title = title_node.text(strip=True)
            if "shop on ebay" in title.lower():
                continue
            price = _parse_price(price_node.text())
            if not price or price <= 0:
                continue
            url = link_node.attributes.get("href", "")
            lid = re.search(r"/itm/(\d+)", url)
            listing_id = lid.group(1) if lid else hashlib.md5(url.encode()).hexdigest()[:12]
            hits.append(LiveHit("ebay", listing_id, title, price, "USD", url, "asking", datetime.now(timezone.utc)))
            if len(hits) >= limit:
                break
    except Exception:
        pass
    return hits

def scan_spreads(queries: list[str], min_profit: Decimal = Decimal("18"), min_pct: Decimal = Decimal("0.20")) -> list[dict]:
    flagged = []
    for q in queries:
        sold = ebay_sold(q, 50) + yahoo_jp_closed(q, 30)
        nets = [net(h.price, h.source) for h in sold if h.kind == "sold"]
        if len(nets) < 5:
            continue
        nets.sort()
        low_net = nets[len(nets) // 5]

        actives = ebay_active(q, 30)
        for a in actives:
            ask_net = net(a.price, a.source)
            profit = low_net - ask_net
            pct = profit / ask_net if ask_net > 0 else Decimal("0")
            if profit >= min_profit and pct >= min_pct:
                flagged.append({
                    "query": q,
                    "title": a.title[:100],
                    "ask": float(a.price),
                    "ask_net": float(ask_net),
                    "comp_low_net": float(low_net),
                    "profit": float(profit),
                    "pct": float(pct),
                    "url": a.url,
                    "n_comps": len(nets),
                    "listing_id": a.listing_id,
                })
    # Highest profit first
    flagged.sort(key=lambda x: x["profit"], reverse=True)
    return flagged

if __name__ == "__main__":
    qs = sys.argv[1:] or ["LOB-001 Dark Magician", "SDK-001 Blue-Eyes"]
    results = scan_spreads(qs)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n{len(results)} spreads cleared the filter.")
