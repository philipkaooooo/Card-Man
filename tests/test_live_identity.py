"""Cross-checks against pinned LIVE responses (fetched 2026-11-05; see tests/data/*/PROVENANCE or headers).

No network here: a fake HTTP layer serves the pinned YGOResources / YGOPRODeck bodies and the
Konami rows parsed from the live page.
"""
import json
from pathlib import Path

from cardman.evidence import RawEvidenceStore
from cardman.identity import ListingSignals, resolve
from cardman.identity_cache import IdentityCache
from cardman.konami_db import KonamiRelease, parse_card_page
from cardman.live_identity import Http, LiveIdentity, konami_only_printings
from cardman.models import IdentityStatus, Region
from tests.real_catalog import real_catalog

DATA = Path(__file__).parent / "data"


class PinnedHttp(Http):
    def __init__(self):
        super().__init__(min_interval_s=0)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if url.endswith("/data/card/4007"):
            return (DATA / "ygoresources" / "4007.json").read_bytes(), {"x-cache-revision": "32947"}
        if "cardinfo.php?id=89631139" in url:
            return (DATA / "ygoprodeck" / "89631139.json").read_bytes(), {}
        if url.endswith("checkDBVer.php"):
            return b'[{"database_version":"147.67"}]', {}
        raise AssertionError(f"unexpected fetch {url}")


def live(tmp_path, monkeypatch):
    cache = IdentityCache(tmp_path / "c.sqlite")
    li = LiveIdentity(cache.db, RawEvidenceStore(tmp_path / "raw"), PinnedHttp())
    rows = [KonamiRelease(**r) for r in json.loads((DATA / "konami" / "4007_releases.json").read_text())["releases"]]
    monkeypatch.setattr(li, "konami_releases", lambda kid: rows)
    return cache, li


def bewd():
    return [p for p in real_catalog().by_family["fam:k4007"]]


def verdicts(checks, pid_part, source):
    return {c.printing_id: (c.verdict, c.detail) for c in checks if pid_part in c.printing_id and c.source == source}


def test_konami_parser_on_real_excerpt():
    rows = parse_card_page((DATA / "konami" / "4007_excerpt.html").read_text(encoding="utf-8"))
    assert rows[0].code == "YAS3-JP003" and rows[0].date == "2026-10-31" and rows[0].rarity_short == "UR"


def test_konami_lists_vintage_codeless_starter_box():
    rows = json.loads((DATA / "konami" / "4007_releases.json").read_text())["releases"]
    codeless = {(r["date"], r["product"]) for r in rows if not r["code"]}
    assert ("1999-03-18", "STARTER BOX") in codeless and ("1999-03-06", "STARTER BOX 劇場限定版") in codeless


def test_jp_codes_confirmed_by_konami_with_date(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    checks = li.check_family(4007, ["89631139"], bewd())
    lb = verdicts(checks, ":LB-01:", "konami_db")
    (v, d), = lb.values()
    assert v == "CONFIRMED" and d["release_date"] == "2000-05-18"
    # YGOResources lists almost no JP prints; that is a coverage gap, not a contradiction
    assert all(v == "NOT_COVERED" for v, _ in verdicts(checks, ":LB-01:", "ygoresources").values())


def test_shared_code_date_is_not_attributed_to_one_product(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    checks = li.check_family(4007, ["89631139"], bewd())
    lob = verdicts(checks, ":LOB-EN001:", "ygoresources")
    assert len(lob) == 2
    assert all(v == "CONFIRMED" and "release_date" not in d and d["code_date"] == "2023-04-21"
               for v, d in lob.values())
    ske = verdicts(checks, ":SKE-001:", "ygoresources")
    assert [d.get("release_date") for _, d in ske.values()] == ["2004-03-01"]


def test_lob_001_is_also_an_asian_english_code(tmp_path, monkeypatch):
    cache, li = live(tmp_path, monkeypatch)
    checks = li.check_family(4007, ["89631139"], bewd())
    (v, d), = verdicts(checks, ":LOB-001:", "ygoresources").values()
    assert "Asian-English" in d["warning"]
    ae = li.asian_english_printings(4007, "fam:k4007", "art:pw89631139")
    assert {p.set_code for p in ae} == {"LOB-001", "SDK-001", "SDRB-AE001"}
    assert all(p.region == Region.ASIAN_EN_OCG_QUARANTINE and p.identity_status == "incomplete" for p in ae)


def test_lob_001_english_listing_cannot_resolve_past_asian_english(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    ae = li.asian_english_printings(4007, "fam:k4007", "art:pw89631139")
    cat = real_catalog()
    from cardman.identity import Catalog
    cat2 = Catalog(list(cat.families.values()), cat.printings + ae)
    res = resolve(ListingSignals(title="Blue-Eyes White Dragon LOB-001", printed_set_code="LOB-001",
                                 card_language="en", verified_edition="1st"), cat2)
    assert res.status == IdentityStatus.CANDIDATES
    assert {p.region for p in res.candidates} == {Region.EN_TCG, Region.ASIAN_EN_OCG_QUARANTINE}


def test_ygoprodeck_disagreement_recorded_not_resolved(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    checks = li.check_family(4007, ["89631139"], bewd())
    gb = verdicts(checks, "legendary-collection-gameboard-edition", "ygoprodeck")
    assert [v for v, _ in gb.values()] == ["MISMATCH"]


def test_konami_only_printings_fill_catalog_gaps(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    known = {p.set_code for p in bewd() if p.set_code and p.region == Region.JP_OCG}
    added = konami_only_printings(li, 4007, "fam:k4007", "art:pw89631139", known)
    codes = {p.set_code for p in added}
    assert {"KA-04", "KA-05"} <= codes          # Structure Deck: Kaiba holds three BEWD numbers
    assert "2020-EN001" not in codes            # EN code on the JP page is not a JP print
    assert len({p.id for p in added}) == len(added)


def test_cached_after_first_fetch(tmp_path, monkeypatch):
    _, li = live(tmp_path, monkeypatch)
    li.check_family(4007, ["89631139"], bewd())
    n = len(li.http.urls)
    li.check_family(4007, ["89631139"], bewd())
    assert len(li.http.urls) == n
    assert li.ygor_revision() == "32947"
