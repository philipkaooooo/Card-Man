"""Daily TCGplayer ask feed for the first 151 characters: one cheapest Near Mint page per version.

Run from the live tree, with the venv python:

  PYTHONPATH=. /tmp/cardman-venv/bin/python scripts/tcg_feed_loop.py

Each pass stores the asks with the day it saw them; the daily ask table joins them to the catalog by
product id and finish, so the character page, card page and market read the same asks.
"""
import sys
import time
from datetime import datetime, timedelta, timezone

from cardman.tcgplayer import feed


def log(s):
    print(datetime.now().strftime("%m-%d %H:%M:%S"), s, flush=True)


def main() -> int:
    while True:
        try:
            stats = feed("data/store/evidence.sqlite", workers=6, pause=0.05, log=log)
            log(f"pass {stats}")
        except Exception as exc:
            log(f"raise {type(exc).__name__}: {exc}")
            time.sleep(600)
            continue
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=21, minute=30, second=0, microsecond=0)
        log(f"next pass {nxt.isoformat()}")
        time.sleep(max(300, (nxt - now).total_seconds()))


if __name__ == "__main__":
    sys.exit(main())
