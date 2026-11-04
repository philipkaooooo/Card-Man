"""Code-verification fixtures. NOT market evidence and NOT yet source-verified.

Printings below are entered from well-known documentation but carry
verified_against_source=False until cross-checked against Konami/YGOResources/
YGOPRODeck (blocked by network policy on 2026-11-04). The STRUCT_ fixtures are
purely structural (same code, several rarities) and use a placeholder card.
"""
from datetime import datetime, timezone
from decimal import Decimal

from cardman.identity import Catalog
from cardman.models import ArtworkVariant, CardFamily, EvidenceType, MarketObservation, Printing, Region

UTC = timezone.utc

BEWD = CardFamily("fam:bewd", {"en": "Blue-Eyes White Dragon", "ja": "青眼の白龍"},
                  konami_id=4007, passcode="89631139", aliases=("BEWD", "ブルーアイズ・ホワイト・ドラゴン"))
DM = CardFamily("fam:dm", {"en": "Dark Magician", "ja": "ブラック・マジシャン"}, passcode="46986414")
STRUCT = CardFamily("fam:struct", {"en": "Fixture Card A"})

ART = ArtworkVariant("art:bewd:1", "fam:bewd", "bewd-original")

P_LOB_1ST = Printing("p:LOB-EN001:UR:1st", "fam:bewd", "art:bewd:1", Region.EN_TCG, "en", "LOB", "Ultra Rare",
                     set_code="LOB-EN001", edition="1st")
P_LOB_UNL = Printing("p:LOB-EN001:UR:unl", "fam:bewd", "art:bewd:1", Region.EN_TCG, "en", "LOB", "Ultra Rare",
                     set_code="LOB-EN001", edition="Unlimited")
P_JP_VOL1 = Printing("p:JP:Vol1:BEWD:UR", "fam:bewd", "art:bewd:1", Region.JP_OCG, "ja", "Vol.1", "Ultra Rare",
                     set_code=None, release_date="1999-02-04")
P_DM_LOB_1ST = Printing("p:LOB-EN005:UR:1st", "fam:dm", "art:dm:1", Region.EN_TCG, "en", "LOB", "Ultra Rare",
                        set_code="LOB-EN005", edition="1st")
STRUCT_SR = Printing("p:STRUCT-EN001:SR", "fam:struct", "art:s:1", Region.EN_TCG, "en", "STRUCT", "Super Rare",
                     set_code="STRC-EN001", edition="1st")
STRUCT_QCSR = Printing("p:STRUCT-EN001:QCSR", "fam:struct", "art:s:1", Region.EN_TCG, "en", "STRUCT",
                       "Quarter Century Secret Rare", set_code="STRC-EN001", edition="1st")
STRUCT_AE = Printing("p:STRUCT-AE001", "fam:struct", "art:s:1", Region.ASIAN_EN_OCG_QUARANTINE, "en", "STRUCT-AE",
                     "Super Rare", set_code="STRC-AE001")

CATALOG = Catalog([BEWD, DM, STRUCT], [P_LOB_1ST, P_LOB_UNL, P_JP_VOL1, P_DM_LOB_1ST, STRUCT_SR, STRUCT_QCSR, STRUCT_AE])


def obs(i, et, amount, *, printing=P_LOB_1ST.id, when=datetime(2026, 10, 1, tzinfo=UTC), **kw):
    base = dict(id=f"o{i}", evidence_type=et, source=kw.pop("source", "fixture"), original_amount=Decimal(amount),
                currency=kw.pop("currency", "TWD"), event_time=when, capture_time=when,
                source_url=f"fixture://{i}", printing_candidates=(printing,), synthetic_fixture=True)
    base.update(kw)
    return MarketObservation(**base)
