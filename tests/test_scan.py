"""Listing triage on real documented printings with synthetic sales (code test, not market evidence)."""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from cardman.fx import FxRate
from cardman.models import EvidenceType
from cardman.scan import scan
from cardman.store import Store
from tests.fixtures import obs
from tests.real_catalog import real_catalog
from tests.test_store import NOW

QCCP = "p:JP_OCG:QCCP-JP001:quarter-century-chronicle-side-pride:{}@89631139"
UR, QCSR = QCCP.format("ultra-rare"), QCCP.format("quarter-century-secret-rare")
SOLD = EvidenceType.MARKETPLACE_MARKED_SOLD


@pytest.fixture
def st():
    s = Store(":memory:")
    s.add_fx(FxRate("USD", "JPY", D("150"), NOW - timedelta(days=1), "test"), now=NOW)
    for i, (pid, amt, src) in enumerate([(UR, "3000", "mercari"), (UR, "3300", "mercari"), (UR, "3150", "yahoo"),
                                         (QCSR, "45000", "mercari"), (QCSR, "48000", "yahoo"), (QCSR, "46500", "mercari")]):
        s.add_observation(obs(i, SOLD, amt, printing=pid, currency="JPY", source=src,
                              when=NOW - timedelta(hours=10 - i)), now=NOW)
    return s


def run(st, title, ask, cur="USD", **kw):
    return scan(title=title, ask=D(ask) if ask is not None else None, ask_currency=cur, store=st,
                catalog=real_catalog(), now=NOW, include_synthetic=True, **kw)


def test_rarity_upside_on_shared_code(st):
    r = run(st, "青眼の白龍 QCCP-JP001 ウルトラ", "30")
    assert {c.printing.id for c in r.candidates} >= {UR, QCSR}
    up = [f for f in r.findings if f.kind == "upside" and "Quarter Century" in f.title]
    assert up and up[0].money > D("250")
    assert r.verdict == "LOTTERY" and "Quarter Century Secret Rare" in r.headline
    assert r.worst < 0 < r.best and r.swing == r.best - r.worst
    assert r.next_evidence  # names the photo that settles it


def test_cheap_enough_for_worst_case_is_a_look(st):
    r = run(st, "青眼の白龍 QCCP-JP001 ウルトラ", "3")
    assert r.verdict == "LOOK" and r.worst > 0


def test_ask_in_other_currency_and_far_under_market(st):
    r = run(st, "青眼の白龍 QCCP-JP001 クオシク", "3000", cur="JPY")
    assert r.ask_home == D("20")
    assert any(f.title == "Far under every recent sale" for f in r.findings)


def test_never_printed_rarity_is_a_mismatch(st):
    r = run(st, "青眼の白龍 QCCP-JP001 Starlight Rare", "30")
    assert any(f.kind == "mismatch" and "never printed as Starlight Rare" in f.title for f in r.findings)


def test_proxy_is_blocked_and_lot_needs_split(st):
    assert run(st, "Blue-Eyes White Dragon orica QCCP-JP001", "5").verdict == "BLOCKED"
    r = run(st, "青眼の白龍 QCCP-JP001 x3", "5")
    assert r.verdict == "NEED_DATA" and "lot of 3" in r.headline


def test_market_gap_and_no_sales(st):
    r = run(st, "青眼の白龍 QCCP-JP001 ウルトラ", "30")
    assert not any("Price gap" in f.title for f in r.findings)  # mercari vs yahoo within 25%
    r = run(st, "Dark Magician Girl", "30")
    assert r.verdict == "NEED_DATA" and r.priority == 0


def test_no_code_spread_across_prints(st):
    r = run(st, "青眼の白龍 美品", "30")
    assert any(f.title == "Title does not say which print" for f in r.findings)
