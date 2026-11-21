"""Condition normalization and condition-aware comp selection. Made-up data only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from cardman import conditions
from cardman.models import EvidenceType as ET
from cardman.opportunity import PriceBook
from tests.fixtures import P_LOB_1ST as P, obs

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def sale(i, amount, days, **kw):
    when = NOW - timedelta(days=days)
    kw.setdefault("synthetic_fixture", False)
    return obs(i, ET.MARKETPLACE_MARKED_SOLD, str(amount), when=when, capture_time=when, **kw)


class St:
    def __init__(self, rows):
        self.rows = rows

    def observations_for(self, pid, as_of):
        return self.rows


class Fx:
    def table(self, pairs, moments):
        raise AssertionError("all TWD; no FX should be asked for")


def book(rows):
    return PriceBook(St(rows), Fx(), {"home_currency": "TWD"}, NOW)


def test_normalize_known_conditions_and_refuses_coarse_ones():
    assert conditions.normalize("NM") == "NM"
    assert conditions.normalize("Near Mint") == "NM"
    assert conditions.normalize("lightly played") == "LP"
    assert conditions.normalize("PSA 10") is None          # a grade is not a raw condition
    assert conditions.normalize("Used") is None
    assert conditions.normalize("") is None


def test_condition_ladder_hits_the_target_not_the_comp():
    nm = sale("nm", 100, 1, observed_condition="NM")
    lp = sale("lp", 78, 2, observed_condition="LP")
    v = book([nm, lp]).value(P.id, condition="NM")
    assert v.same_condition == 1 and v.adjusted == 1
    assert sorted(round(s.price, 2) for s in v.sales) == [100.0, 100.0]      # LP sale restated to NM


def test_enough_same_condition_sales_need_no_restating():
    rows = [sale(f"nm{i}", 100, i + 1, observed_condition="NM") for i in range(3)]
    rows += [sale(f"lp{i}", 60, i + 5, observed_condition="LP") for i in range(3)]
    v = book(rows).value(P.id, condition="NM")
    assert v.same_condition == 3 and v.adjusted == 0
    assert {s.price for s in v.sales} == {100.0}


def test_unknown_condition_comp_is_the_nm_baseline():
    rows = [sale(f"u{i}", 100, i + 1) for i in range(3)]     # no condition named on any comp
    v = book(rows).value(P.id, condition="LP")
    assert v.adjusted == 3
    assert {round(s.price, 2) for s in v.sales} == {78.0}


def test_graded_slabs_ignore_raw_condition():
    rows = [sale(f"g{i}", 900, i + 1, grade_company="PSA", grade="10", observed_condition="NM") for i in range(3)]
    v = book(rows).value(P.id, "PSA", "10", "LP")
    assert v.condition is None and v.adjusted == 0
    assert {s.price for s in v.sales} == {900.0}
