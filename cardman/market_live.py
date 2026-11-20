"""Keyword sold-scrape. Removed.

An eBay or Yahoo search mixes printings, conditions and lots. The HTML is not a sale.
Asks are read per SKU in ``cardman.tcgplayer``. Sold comps come from the store and from
a SKU's cleared buckets.
"""
from __future__ import annotations


class Removed(RuntimeError):
    pass


_WHY = "removed: a search query is not one printing, and a scraped tile is not a sale"


def ebay_sold(*_a, **_k):
    raise Removed(_WHY)


def yahoo_jp_closed(*_a, **_k):
    raise Removed(_WHY)


def net(*_a, **_k):
    raise Removed(_WHY)


def scan(*_a, **_k):
    raise Removed(_WHY)
