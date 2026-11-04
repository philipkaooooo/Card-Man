"""Identity source adapters (CLAUDE.md sections 1-2).

Each adapter turns one source record into CardFamily / ArtworkVariant / Printing rows and
keeps a reference back to the exact source revision. Nothing here guesses a printing the
source does not list.

* YAML Yugi (github.com/DawnbrandBots/yaml-yugi): compiled from Yugipedia, one file per
  passcode. Secondary source: it lists set code, product and rarities per region, but not
  editions (1st / Unlimited / Limited), release dates or Asian-English prints.
  Reachable through the git proxy; tested 2026-11-04.
* YGOPRODeck v7 and YGOResources: primary-ish HTTP APIs. Adapters are written against their
  public docs but are NOT yet tested against a live response (host blocked by the network
  policy on 2026-11-04). Raw responses are stored before any parsing.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .models import ArtworkVariant, CardFamily, Printing, Region

YAML_YUGI_NORMALIZER_VERSION = "yaml-yugi-norm-0.2.0"

# YAML Yugi region keys we trade. Other languages (de/fr/it/es/pt/ko/zh) are out of scope.
_YY_REGION = {"en": (Region.EN_TCG, "en"), "ja": (Region.JP_OCG, "ja")}

# Abbreviations that appear as rarity labels in the source; the original label is kept as evidence.
_RARITY_ABBREV = {"UR": "Ultra Rare", "StR": "Starlight Rare"}


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w]+", "-", s).strip("-")
    return s or "x"


def _strip_ruby(s: str) -> str:
    return re.sub(r"<rt>.*?</rt>", "", s).replace("<ruby>", "").replace("</ruby>", "")


def _ruby_reading(s: str) -> str | None:
    if "<ruby>" not in s:
        return None
    return re.sub(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", r"\2", s)


@dataclass(frozen=True)
class NormalizedCard:
    family: CardFamily
    artwork: ArtworkVariant
    printings: tuple[Printing, ...]
    passcode: str
    source_ref: str


def family_id_for(konami_id: int | None, password: int | str | None) -> str:
    if konami_id:
        return f"fam:k{konami_id}"
    return f"fam:pw{password}"


def normalize_yaml_yugi(rec: dict, source_ref: str, file_key: str | None = None) -> NormalizedCard | None:
    """One YAML Yugi card file -> family, artwork (per passcode) and printings.

    Konami ID is the family key: alternate-artwork cards (e.g. Dark Magician 46986414 and
    36996508) share a Konami ID but have separate passcodes, so each passcode is an artwork.
    Several illustrations under one passcode are not mapped to printings by this source.
    """
    names = rec.get("name") or {}
    if not names.get("en") and not names.get("ja"):
        return None
    kid, pw = rec.get("konami_id"), rec.get("password")
    fam_id = family_id_for(kid, pw if pw is not None else f"file-{file_key}")
    ja = names.get("ja")
    aliases = tuple(a for a in (_ruby_reading(ja) if ja else None, names.get("ja_romaji")) if a)
    family = CardFamily(
        id=fam_id,
        names={k: (_strip_ruby(v) if k == "ja" else v) for k, v in names.items() if k in ("en", "ja") and v},
        konami_id=kid, passcode=str(pw) if pw is not None else None, aliases=aliases,
    )
    # Cards without a passcode (some promos/tokens) are keyed by their source file instead.
    pw_s = str(pw) if pw is not None else f"file-{file_key or kid}"
    n_illus = len({i.get("illustration") or i.get("image") for i in rec.get("images") or []})
    art = ArtworkVariant(id=f"art:pw{pw_s}", family_id=fam_id, visual_key=f"passcode:{pw_s}",
                         source_refs=(source_ref,))
    art_note = (f"{n_illus} illustrations under passcode {pw_s}; source does not map them to printings"
                if n_illus > 1 else None)
    printings: dict[str, Printing] = {}
    for key, (region, lang) in _YY_REGION.items():
        for s in (rec.get("sets") or {}).get(key) or []:
            code = (s.get("set_number") or "").strip().upper() or None
            product = (s.get("set_name") or "").strip()
            rarities = s.get("rarities") or []
            if not rarities:
                rarities = [None]
            for raw_rar in rarities:
                notes = []
                status = "documented"
                rar = raw_rar
                if raw_rar is None:
                    rar, status = "UNKNOWN", "incomplete"
                    notes.append("source lists no rarity for this set entry")
                elif raw_rar in _RARITY_ABBREV:
                    rar = _RARITY_ABBREV[raw_rar]
                    notes.append(f"source rarity label '{raw_rar}' normalized to '{rar}'")
                if not code:
                    notes.append("no printed set code (vintage or special product)")
                elif "?" in code:
                    # e.g. 'MAMS-EN???' before a set list is final; never resolve against a placeholder
                    status = "incomplete"
                    notes.append(f"placeholder set number '{code}' (set list not final)")
                if art_note:
                    notes.append(art_note)
                # passcode is part of the key: alternate artworks can share code, product and rarity
                pid = f"p:{region.value}:{code or '-'}:{slug(product)}:{slug(rar)}@{pw_s}"
                if pid in printings:
                    continue
                printings[pid] = Printing(
                    id=pid, family_id=fam_id, artwork_id=art.id, region=region, language=lang,
                    product_id=f"{region.value}:{slug(product)}", product_name=product, rarity=rar,
                    set_code=code, edition=None, identity_status=status,
                    variant_evidence=tuple(notes), source_refs=(source_ref,),
                    verified_against_source=False,
                )
    return NormalizedCard(family, art, tuple(printings.values()), pw_s, source_ref)


# ---------------------------------------------------------------------------------------------
# HTTP identity APIs. Written from public documentation; not yet exercised against live data.

USER_AGENT = "cardman/0.2 (private research tool)"


def http_get(url: str, timeout: float = 30) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


class YGOPRODeckSource:
    """YGOPRODeck v7 (https://ygoprodeck.com/api-guide/). EN TCG sets only; no editions.

    `card_prices` is ignored on purpose: it is not per-print sold evidence.
    Status: live response shape untested (host blocked 2026-11-04).
    """
    BASE = "https://db.ygoprodeck.com/api/v7"
    name = "ygoprodeck"

    def db_version_url(self) -> str:
        return f"{self.BASE}/checkDBVer.php"

    def cardinfo_url(self, passcode: str) -> str:
        return f"{self.BASE}/cardinfo.php?id={passcode}"

    @staticmethod
    def normalize(card: dict, source_ref: str, konami_id: int | None = None) -> list[Printing]:
        fam = family_id_for(konami_id, card.get("id"))
        out = []
        for s in card.get("card_sets") or []:
            code = (s.get("set_code") or "").upper() or None
            rar = s.get("set_rarity") or "UNKNOWN"
            product = s.get("set_name") or ""
            out.append(Printing(
                id=f"p:EN_TCG:{code or '-'}:{slug(product)}:{slug(rar)}", family_id=fam,
                artwork_id=f"art:pw{card.get('id')}", region=Region.EN_TCG, language="en",
                product_id=f"EN_TCG:{slug(product)}", product_name=product, rarity=rar, set_code=code,
                identity_status="documented" if s.get("set_rarity") else "incomplete",
                source_refs=(source_ref,),
            ))
        return out


class YGOResourcesSource:
    """YGOResources (https://db.ygoresources.com/about/api).

    Cache invalidation is revision based: GET /manifest/{revision} lists what changed since a
    revision. We store raw responses and only normalize once a live payload has been seen.
    Status: untested (host blocked 2026-11-04); no normalizer yet on purpose.
    """
    BASE = "https://db.ygoresources.com"
    name = "ygoresources"

    def manifest_url(self, revision: int) -> str:
        return f"{self.BASE}/manifest/{revision}"

    def card_url(self, konami_id: int) -> str:
        return f"{self.BASE}/data/card/{konami_id}"


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
