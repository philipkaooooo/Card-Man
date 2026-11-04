"""Manual import fallback: fresh (asking) and historical (sold / accepted offer) records stay different classes."""
from datetime import datetime, timezone

import pytest

from cardman.evidence import import_manual_csv, select_comps
from cardman.models import EvidenceType

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
PID = "p:JP_OCG:LB-01:legend-of-blue-eyes-white-dragon-japanese:ultra-rare@89631139"
HEADER = open("examples/manual_import_TEMPLATE.csv", encoding="utf-8").read().strip()


def write(tmp_path, rows):
    p = tmp_path / "m.csv"
    p.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_same_listing_asking_then_accepted_offer(tmp_path):
    p = write(tmp_path, [
        f"a1,ACTIVE_ASKING,ebay,300,USD,2026-10-01T00:00:00+00:00,fixture://l1,{PID},L1,,s1,10,,,NM,false,",
        f"a2,ACCEPTED_OFFER,ebay_product_research,240,USD,2026-10-05T00:00:00+00:00,fixture://l1,{PID},L1,ord1,s1,10,,,NM,false,",
    ])
    obs = import_manual_csv(p, NOW)
    assert {o.evidence_type for o in obs} == {EvidenceType.ACTIVE_ASKING, EvidenceType.ACCEPTED_OFFER}
    assert all(o.synthetic_fixture for o in obs)   # non-http source URLs are never market evidence
    comps, rejected = select_comps(obs, PID, as_of=NOW, include_synthetic=True)
    assert [c.id for c in comps] == ["a2"]
    assert ("a1", "ACTIVE_ASKING is not a sale") in rejected
    assert select_comps(obs, PID, as_of=NOW)[0] == []   # synthetic rows refused by default


def test_unknown_evidence_type_is_refused(tmp_path):
    p = write(tmp_path, [f"x,SOLD,ebay,1,USD,2026-10-01T00:00:00+00:00,https://www.ebay.com/itm/1,{PID},,,,,,,,,"])
    with pytest.raises(ValueError):
        import_manual_csv(p, NOW)


def test_missing_source_url_is_refused(tmp_path):
    p = write(tmp_path, [f"x,ACTIVE_ASKING,ebay,1,USD,2026-10-01T00:00:00+00:00,,{PID},,,,,,,,,"])
    with pytest.raises(ValueError):
        import_manual_csv(p, NOW)
