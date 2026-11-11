"""Account deletion reaches every store and raw file, stays precise, and only confirms after finishing. Data here is made up."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from cardman.ebay_deletion import REDACTED, purge, purge_database, purge_files, sync
from cardman.models import EvidenceType as ET, MarketObservation
from cardman.store import Store, replay
from decimal import Decimal as D

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def ob(i, seller):
    return MarketObservation(id=f"o{i}", evidence_type=ET.ACTIVE_ASKING, source="ebay_browse:EBAY_US", original_amount=D(100), currency="USD", event_time=NOW,
                             capture_time=NOW, source_url="https://example.test/x", printing_candidates=("p",), listing_id=str(i), seller_ref=seller)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "s.sqlite"
    st = Store(p)
    st.add_observation(ob(1, "gone_user"), now=NOW)
    st.add_observation(ob(2, "gone_user_2"), now=NOW)
    st.add_observation(ob(3, "other"), now=NOW)
    with st.db:
        st.db.execute("CREATE TABLE listing (key TEXT PRIMARY KEY, seller TEXT, data_json TEXT)")
        st.db.execute("INSERT INTO listing VALUES ('a','gone_user','{\"note\":\"from gone_user\"}'),('b','other','{}')")
        st.db.execute("CREATE TABLE market_signal (id TEXT PRIMARY KEY, kind TEXT, printing_id TEXT, at TEXT, detail_json TEXT)")
        st.db.execute("INSERT INTO market_signal VALUES ('SELLER_ACTIVITY:gone_user:d','SELLER_ACTIVITY','gone_user','t','{}'),('X:other:d','X','other','t','{}')")
    st.db.close()
    return p


def test_seller_is_removed_everywhere_and_neighbors_are_untouched(db):
    r = purge_database(db, ["gone_user"])
    assert r.rows >= 3
    st = Store(db)
    assert st.original("o1").seller_ref is None
    assert st.original("o2").seller_ref == "gone_user_2"     # a longer name is another person
    assert st.original("o3").seller_ref == "other"
    assert st.db.execute("SELECT seller FROM listing WHERE key='a'").fetchone() == (None,)
    assert REDACTED in st.db.execute("SELECT data_json FROM listing WHERE key='a'").fetchone()[0]
    assert [x[0] for x in st.db.execute("SELECT id FROM market_signal")] == ["X:other:d"]


def test_nothing_recoverable_remains_in_the_file(db):
    purge_database(db, ["gone_user"])
    assert not __import__("re").search(rb"gone_user(?!_2)", db.read_bytes())


def test_purged_observation_still_passes_the_stores_write_once_check(db):
    purge_database(db, ["gone_user"])
    st = Store(db)
    stored = st.db.execute("SELECT content_hash, data_json FROM observation WHERE id='o1'").fetchone()
    from cardman.store import content_hash
    assert stored[0] == content_hash(json.loads(stored[1]))


def test_identifier_inside_a_snapshot_is_scrubbed_and_snapshot_stays_readable(tmp_path):
    p = tmp_path / "s.sqlite"
    st = Store(p)
    vin = {"comp_prices_home": [], "ceiling_inputs": None}
    from cardman.store import compute_valuation
    sid = st.save_snapshot(listing_ref="l", now=NOW, expires_at=NOW, stage="OBSERVE", printing_ids=[],
                           payload={"report": {"note": "seen from gone_user"}, "valuation_inputs": vin, "valuation": compute_valuation(vin), "listing": {}})
    st.db.close()
    assert purge_database(p, ["gone_user"]).snapshots == 1
    st = Store(p)
    assert st.snapshot(sid)["report"]["note"] == f"seen from {REDACTED}"
    assert replay(st, sid)["matches"]


def test_raw_pages_are_rewritten(tmp_path):
    d = tmp_path / "raw" / "ebay_browse"
    d.mkdir(parents=True)
    (d / "a.bin").write_text(json.dumps({"seller": {"username": "gone_user"}, "other": "gone_user_2"}))
    (d / "b.bin").write_bytes(b"\xff\xfe binary untouched")
    assert purge_files(tmp_path / "raw", ["gone_user"]) == 1
    assert "gone_user\"" not in (d / "a.bin").read_text() and "gone_user_2" in (d / "a.bin").read_text()
    assert (d / "b.bin").read_bytes() == b"\xff\xfe binary untouched"


def test_regex_characters_in_a_name_are_literal(db):
    assert purge_database(db, ["g.ne_user"]).rows == 0


def test_log_keeps_ids_and_counts_only(db, tmp_path):
    out = purge([{"notificationId": "n1", "username": "gone_user", "userId": "U1", "eiasToken": "E1"}], databases=[db], raw=tmp_path / "none", log_db=db)
    assert out["processed"] == ["n1"]
    row = sqlite3.connect(db).execute("SELECT * FROM ebay_deletion_log").fetchone()
    assert row[0] == "n1" and "gone_user" not in json.dumps(row)


def test_sync_confirms_only_after_purging_and_is_off_without_settings(db, tmp_path, monkeypatch):
    monkeypatch.delenv("EBAY_DELETIONS_URL", raising=False)
    monkeypatch.delenv("EBAY_DELETIONS_SECRET", raising=False)
    assert sync()["configured"] is False
    queue = [{"notificationId": "n1", "username": "gone_user"}]
    seen = []

    def call(url, secret, body=None):
        if body is None:
            return {"deletions": list(queue)}
        assert Store(db).original("o1").seller_ref is None     # already gone when eBay's queue entry is released
        seen.append(body)
        queue.clear()
        return {"removed": 1}
    out = sync(url="u", secret="s", databases=[db], raw=tmp_path / "none", call=call)
    assert out["processed"] == 1 and seen == [{"processed": ["n1"]}] and not queue


def test_failed_purge_is_not_confirmed(db, tmp_path, monkeypatch):
    import cardman.ebay_deletion as m
    released = []

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(m, "purge_files", boom)

    def call(url, secret, body=None):
        if body is not None:
            released.append(body)
        return {"deletions": [{"notificationId": "n1", "username": "gone_user"}]}
    with pytest.raises(OSError):
        sync(url="u", secret="s", databases=[db], raw=tmp_path, call=call)
    assert not released


def test_stored_hash_of_a_seller_is_erased_too(tmp_path):
    from cardman.ebay_privacy import seller_token
    p = tmp_path / "s.sqlite"
    st = Store(p)
    st.add_observation(ob(1, seller_token("hashed_user")), now=NOW)
    st.add_observation(ob(2, seller_token("someone_else")), now=NOW)
    st.db.close()
    purge([{"notificationId": "n", "username": "hashed_user"}], databases=[p], raw=tmp_path / "none", log_db=p)
    st = Store(p)
    assert st.original("o1").seller_ref is None and st.original("o2").seller_ref == seller_token("someone_else")
