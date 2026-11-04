"""eBay Browse adapter. Payloads below are SYNTHETIC, shaped after eBay's documented item_summary
response, to exercise code paths only. No live eBay request has been made (no keys; host blocked)."""
import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from cardman.ebay_browse import EbayBrowse, NotConfigured, SourceUnavailable
from cardman.evidence import RawEvidenceStore, select_comps
from cardman.models import EvidenceType
from tests.real_catalog import real_catalog

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def item(i, **kw):
    base = {"itemId": f"v1|{i}|0", "legacyItemId": str(i), "title": "Blue-Eyes White Dragon LOB-EN001 Ultra Rare",
            "price": {"value": "100.00", "currency": "USD"}, "buyingOptions": ["FIXED_PRICE"],
            "itemWebUrl": f"https://www.ebay.com/itm/{i}", "seller": {"username": f"s{i}"},
            "condition": "Ungraded", "itemCreationDate": "2026-10-30T00:00:00.000Z",
            "shippingOptions": [{"shippingCost": {"value": "5.00", "currency": "USD"}}]}
    base.update(kw)
    return base


class FakeOpener:
    def __init__(self, pages, fail=None):
        self.pages, self.fail, self.calls = list(pages), list(fail or []), []

    def __call__(self, req, timeout=30):
        self.calls.append(req.full_url)
        if self.fail:
            raise self.fail.pop(0)
        if "oauth2/token" in req.full_url:
            return io.BytesIO(json.dumps({"access_token": "t", "expires_in": 7200}).encode())
        return io.BytesIO(json.dumps(self.pages.pop(0)).encode())


def client(tmp_path, opener, **kw):
    return EbayBrowse(RawEvidenceStore(tmp_path / "raw"), client_id="id", client_secret="secret", opener=opener,
                      sleep=lambda s: None, min_interval_s=0, **kw)


def test_no_keys_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    with pytest.raises(NotConfigured):
        EbayBrowse(RawEvidenceStore(tmp_path)).search("x")


def test_live_listings_are_never_sold_comps(tmp_path):
    pages = [{"total": 3, "itemSummaries": [
        item(1),
        item(2, buyingOptions=["FIXED_PRICE", "BEST_OFFER"]),
        item(3, buyingOptions=["AUCTION"], currentBidPrice={"value": "40.00", "currency": "USD"}, bidCount=3),
    ]}]
    res = client(tmp_path, FakeOpener(pages)).search("blue-eyes", catalog=real_catalog(), now=NOW, synthetic=True)
    types = [o.evidence_type for o in res.observations]
    assert types == [EvidenceType.ACTIVE_ASKING, EvidenceType.ACTIVE_ASKING, EvidenceType.OBSERVED_BID]
    assert "Best Offer" in res.observations[1].status_reason
    assert res.observations[0].shipping_amount is not None
    # Seller-typed code narrows to the two LOB-EN001 products but never resolves.
    assert len(res.observations[0].printing_candidates) == 2
    for pid in res.observations[0].printing_candidates:
        comps, rejected = select_comps(res.observations, pid, as_of=NOW, include_synthetic=True)
        assert comps == [] and all("not a sale" in r or "identity" in r for _, r in rejected)
    assert all(o.synthetic_fixture for o in res.observations)
    assert res.raw_refs and (tmp_path / "raw" / f"{res.raw_refs[0]}.bin").exists()


def test_pagination_and_offset_shift_dedup(tmp_path):
    pages = [{"total": 3, "itemSummaries": [item(1), item(2)], "next": "https://api.ebay.com/next1"},
             {"total": 3, "itemSummaries": [item(2), item(3)]}]
    res = client(tmp_path, FakeOpener(pages)).search("x", now=NOW)
    assert [o.listing_id for o in res.observations] == ["1", "2", "3"] and res.pages == 2


def test_outage_raises_instead_of_returning_no_supply(tmp_path):
    err = urllib.error.URLError("proxy 403")
    op = FakeOpener([], fail=[err] * 10)
    with pytest.raises(SourceUnavailable):
        client(tmp_path, op, max_retries=2).search("x", now=NOW)


def test_rate_limit_backs_off_then_succeeds(tmp_path):
    e429 = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    op = FakeOpener([{"total": 1, "itemSummaries": [item(9)]}])
    c = client(tmp_path, op)
    c.token()
    op.fail = [e429]
    res = c.search("x", now=NOW)
    assert len(res.observations) == 1
