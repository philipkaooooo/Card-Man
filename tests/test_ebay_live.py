"""Runner for the live eBay pull, exercised with SYNTHETIC payloads (no network, no keys)."""
import json
import shutil
import urllib.error
from pathlib import Path

from cardman.ebay_browse import EbayBrowse
from cardman.ebay_live import REGISTRY, run
from cardman.evidence import RawEvidenceStore
from cardman.store import Store
from tests.test_ebay_browse import FakeOpener, item


def _reg(tmp_path: Path) -> Path:
    p = tmp_path / "reg.json"
    shutil.copy(REGISTRY, p)
    return p


def _row(p: Path) -> dict:
    return next(r for r in json.loads(p.read_text()) if r["source"] == "ebay_browse")


def test_success_stores_evidence_and_marks_registry(tmp_path):
    reg = _reg(tmp_path)
    page = {"total": 2, "itemSummaries": [item(1), item(2, buyingOptions=["AUCTION"],
                                                       currentBidPrice={"value": "40", "currency": "USD"}, bidCount=3)]}
    raw = RawEvidenceStore(tmp_path / "raw")
    b = EbayBrowse(raw, client_id="x", client_secret="y", opener=FakeOpener([page]), sleep=lambda s: None)
    assert run(tmp_path / "m.sqlite", tmp_path / "raw", 1, ["q"], registry=reg, browse=b) == 0
    row = _row(reg)
    assert row["available"] and row["authenticated"] and row["last_success"] and row["failure_reason"] is None
    n = Store(tmp_path / "m.sqlite").db.execute("select evidence_type, count(*) from observation group by 1").fetchall()
    assert dict(n) == {"ACTIVE_ASKING": 1, "OBSERVED_BID": 1}


def test_missing_keys_recorded(tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    reg = _reg(tmp_path)
    assert run(tmp_path / "m.sqlite", tmp_path / "raw", 1, ["q"], registry=reg) == 2
    row = _row(reg)
    assert not row["available"] and "not set" in row["failure_reason"]


def test_rejected_keys_recorded(tmp_path):
    reg = _reg(tmp_path)
    err = urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
    b = EbayBrowse(RawEvidenceStore(tmp_path / "raw"), client_id="x", client_secret="y",
                   opener=FakeOpener([], fail=[err]), sleep=lambda s: None)
    assert run(tmp_path / "m.sqlite", tmp_path / "raw", 1, ["q"], registry=reg, browse=b) == 3
    assert "401" in _row(reg)["failure_reason"]
