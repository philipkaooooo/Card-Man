"""Keyword spread scan. Removed.

It treated every title in a search as the same card and called the gap a profit.
That number is not used. ``python -m cardman.tcgplayer`` screens one product's SKUs.
"""
from __future__ import annotations

from .market_live import Removed


def scan_spreads(*_a, **_k):
    raise Removed("removed: a search mixes printings. Use cardman.tcgplayer for one product.")


def main() -> None:
    raise SystemExit("removed: python -m cardman.spread does not scan. Use python -m cardman.tcgplayer.")


if __name__ == "__main__":
    main()
