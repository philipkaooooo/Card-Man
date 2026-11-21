"""Bring the three catalogs back when they are missing, then prime each version's own guide price.

  pokemon   data/cache/pkm.sqlite          every English card
  ygo       data/cache/identity.sqlite     every Yu-Gi-Oh! printing
  master    data/cache/catalog.sqlite      those two joined to TCGplayer, one price row per version

A guide price is that version's TCGplayer market price. It is not a sale, and it is never another version's price.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "data" / "cache" / "catalog_boot.json"


def _count(db: Path, sql: str) -> int:
    if not db.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = con.execute(sql).fetchone()[0]
        con.close()
        return int(n or 0)
    except Exception:
        return 0


def missing() -> list[str]:
    """Catalogs that are absent or empty, in the order they have to be built."""
    from .catalog.registry import DB as MASTER
    from .identity_cache import IdentityCache
    from .pkm_catalog import DB as PKM
    out = []
    if _count(PKM, "SELECT COUNT(*) FROM pkm_card") == 0:
        out.append("pkm")
    if _count(ROOT / "data" / "cache" / "identity.sqlite", "SELECT COUNT(*) FROM printing") == 0:
        out.append("ygo")
    if "pkm" not in out and "ygo" not in out and _count(MASTER, "SELECT COUNT(*) FROM printing") == 0:
        out.append("master")
    return out


def write_status(step: str, detail: str = "") -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"step": step, "detail": detail}))


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return {}


def version_market(pid: str) -> tuple[float, str] | None:
    """The latest TCGplayer market price stored for this exact version. None if that version has no row."""
    from .catalog.registry import DB as MASTER
    if not pid or not MASTER.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{MASTER}?mode=ro", uri=True)
        row = con.execute("SELECT market, day FROM market_price WHERE pid=? AND source='tcgplayer' AND market IS NOT NULL "
                          "ORDER BY day DESC LIMIT 1", (pid,)).fetchone()
        con.close()
    except Exception:
        return None
    return (float(row[0]), row[1]) if row else None


def ensure(log=print) -> list[str]:
    """Download and build whatever is missing. Safe to run while the site is open. Returns what it built."""
    built = []
    try:
        if "pkm" in missing():
            write_status("pkm", "downloading the Pokémon card list")
            from .pkm_catalog import build, sync_files
            sync_files(log=log)
            n = build()
            log(f"pokemon catalog: {n} cards")
            built.append("pkm")
        if "ygo" in missing():
            write_status("ygo", "downloading the Yu-Gi-Oh! card list")
            from .identity_cache import IdentityCache, update_checkout
            repo = ROOT / "data" / "cache" / "yaml-yugi"
            update_checkout(repo)
            IdentityCache(ROOT / "data" / "cache" / "identity.sqlite").sync_yaml_yugi(repo)
            built.append("ygo")
        if "master" in missing():
            write_status("master", "matching both lists to TCGplayer and storing one price per version")
            from .catalog import registry, tcgcsv
            from .identity_cache import IdentityCache
            from .pkm_catalog import DB as PKM
            from .pkm_catalog import PkmCatalog
            for game in ("pkm", "ygo"):
                tcgcsv.sync(game, log=log)
            ygo = IdentityCache(ROOT / "data" / "cache" / "identity.sqlite").catalog()
            registry.build(ygo=ygo, pkm=PkmCatalog(PKM, registry=False),
                           tcg_ygo=tcgcsv.latest("ygo"), tcg_pkm=tcgcsv.latest("pkm"), log=log)
            built.append("master")
    except Exception as x:
        write_status("error", f"{type(x).__name__}: {x}")
        raise
    write_status("ready" if not missing() else "error",
                 "each version keeps its own guide price" if not missing() else "a catalog is still missing")
    return built
