"""Catalog built from real YAML Yugi records (tests/data/yaml_yugi, pinned revision in PROVENANCE.json).

These are documented printings, not invented ones. They are identity data only: never market evidence.
"""
import json
from functools import lru_cache
from pathlib import Path

from cardman.identity import Catalog
from cardman.identity_sources import normalize_yaml_yugi

DATA = Path(__file__).parent / "data" / "yaml_yugi"
PROVENANCE = json.loads((DATA / "PROVENANCE.json").read_text(encoding="utf-8"))
REV = PROVENANCE["revision"]


@lru_cache(maxsize=1)
def real_catalog() -> Catalog:
    fams, prints = {}, []
    for f in sorted(DATA.glob("[0-9]*.json")):
        n = normalize_yaml_yugi(json.loads(f.read_text(encoding="utf-8")),
                                f"yaml_yugi@{REV[:12]}:data/cards/{f.name}", file_key=f.stem)
        fams.setdefault(n.family.id, n.family)
        prints += n.printings
    return Catalog(list(fams.values()), prints)
