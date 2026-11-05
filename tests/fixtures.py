"""Code-verification fixtures. NOT market evidence and NOT yet source-verified.

These hand-entered printings exercise code paths (economics, evidence, workflow). They are
not used as identity truth: regression tests on real documented printings live in
tests/test_identity_real.py and use tests/real_catalog.py (YAML Yugi records at a pinned
revision). The STRUCT_ fixtures are purely structural and use a placeholder card.
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
# Corrected 2026-11-05: an earlier version put BEWD in "Vol.1" (1999-02-04). Konami's database lists
# BEWD's first Japanese release as STARTER BOX, 1999-03-18, with no print code (Vol.1 had Dark Magician).
P_JP_STARTER = Printing("p:JP:StarterBox:BEWD:UR", "fam:bewd", "art:bewd:1", Region.JP_OCG, "ja", "Starter Box",
                        "Ultra Rare", set_code=None, release_date="1999-03-18")
P_DM_LOB_1ST = Printing("p:LOB-EN005:UR:1st", "fam:dm", "art:dm:1", Region.EN_TCG, "en", "LOB", "Ultra Rare",
                        set_code="LOB-EN005", edition="1st")
STRUCT_SR = Printing("p:STRUCT-EN001:SR", "fam:struct", "art:s:1", Region.EN_TCG, "en", "STRUCT", "Super Rare",
                     set_code="STRC-EN001", edition="1st")
STRUCT_QCSR = Printing("p:STRUCT-EN001:QCSR", "fam:struct", "art:s:1", Region.EN_TCG, "en", "STRUCT",
                       "Quarter Century Secret Rare", set_code="STRC-EN001", edition="1st")
STRUCT_AE = Printing("p:STRUCT-AE001", "fam:struct", "art:s:1", Region.ASIAN_EN_OCG_QUARANTINE, "en", "STRUCT-AE",
                     "Super Rare", set_code="STRC-AE001")

CATALOG = Catalog([BEWD, DM, STRUCT], [P_LOB_1ST, P_LOB_UNL, P_JP_STARTER, P_DM_LOB_1ST, STRUCT_SR, STRUCT_QCSR, STRUCT_AE])


def obs(i, et, amount, *, printing=P_LOB_1ST.id, when=datetime(2026, 10, 1, tzinfo=UTC), **kw):
    base = dict(id=f"o{i}", evidence_type=et, source=kw.pop("source", "fixture"), original_amount=Decimal(amount),
                currency=kw.pop("currency", "TWD"), event_time=when, capture_time=when,
                source_url=f"fixture://{i}", printing_candidates=(printing,), synthetic_fixture=True)
    base.update(kw)
    return MarketObservation(**base)
