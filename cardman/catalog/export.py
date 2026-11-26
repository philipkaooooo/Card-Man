"""Write the master card list for browsing in Excel: data/exports/catalog_{game}.csv, and .xlsx when openpyxl is
installed. Exports are not committed (data/exports/ is git-ignored)."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .registry import DB, ROOT

OUT = ROOT / "data" / "exports"
COLS = ["pid", "game", "set_id", "set_name", "release", "number", "name", "rarity", "variant", "language", "subset", "is_reprint",
        "reprint_of", "sources", "tcgplayer_product", "tcgplayer_market_usd", "market_day"]


def rows(con, game: str):
    q = ("SELECT p.pid, p.game, p.set_id, p.set_name, p.release, p.number, p.name, p.rarity, p.variant, p.language, p.subset, p.is_reprint, "
         "p.reprint_of, p.sources, (SELECT ext_id FROM xref x WHERE x.pid=p.pid AND x.source='tcgplayer' LIMIT 1), "
         "(SELECT market FROM market_price m WHERE m.pid=p.pid ORDER BY day DESC LIMIT 1), "
         "(SELECT day FROM market_price m WHERE m.pid=p.pid ORDER BY day DESC LIMIT 1) "
         "FROM printing p WHERE p.game=? ORDER BY p.release, p.set_id, p.number")
    return con.execute(q, (game,)).fetchall()


def export(db: Path | str = DB, out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    written = []
    for game in ("pkm", "ygo"):
        data = rows(con, game)
        p = out / f"catalog_{game}.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLS)
            w.writerows(data)
        written.append(p)
        try:
            import openpyxl
            wb = openpyxl.Workbook(write_only=True)
            ws = wb.create_sheet("printings")
            ws.append(COLS)
            for r in data:
                ws.append(list(r))
            miss = wb.create_sheet("needs a look")
            miss.append(["source", "kind", "id", "what"])
            for r in con.execute("SELECT source, kind, ext_id, detail FROM needs_look WHERE game=? ORDER BY kind, detail", (game,)):
                miss.append(list(r))
            x = out / f"catalog_{game}.xlsx"
            wb.save(x)
            written.append(x)
        except ImportError:
            pass
    return written


if __name__ == "__main__":
    for p in export():
        print(p)
