"""A dated quote attaches to one SKU or it does not. It is never a sale."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cardman.evidence import select_comps
from cardman.models import EvidenceType
from cardman.post import Book, PostRegion, read_region, to_observation
from cardman.post import main as post_main
from cardman.store import Store

T = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
DAMAGED = Book(exit_low=Decimal("19"), sales=10, market=Decimal("119.14"), high_day=Decimal("26.60"))
NM = Book(exit_low=Decimal("119.99"), sales=8, market=Decimal("119.14"))


def region(**kw) -> PostRegion:
    base = dict(source="ig:example", posted_at=T, region="photo-1", amount=Decimal("119"),
                currency="USD", amount_in_region=True, printed_code="LOB-001", language="English",
                variant="Unlimited", condition="Damaged", product_id=21792)
    base.update(kw)
    return PostRegion(**base)


def test_a_damaged_card_quoted_at_the_mixed_market_is_a_mirror():
    got = read_region(region(), DAMAGED)
    assert got.attached and got.verdict == "MIRROR"
    assert got.card_key == "tcg:21792|Unlimited|Damaged|English"
    assert got.evidence_type == "SELLER_CLAIM" and "Not a sale" in got.why
    assert "119.14" in got.why and "19" in got.why


def test_the_same_number_on_near_mint_is_the_book_not_a_mirror():
    got = read_region(region(condition="NM", amount=Decimal("119")), NM)
    assert got.verdict == "AT_BOOK"
    assert got.card_key.endswith("|Near Mint|English")


def test_the_high_day_is_not_the_exit_and_a_falling_book_can_be_a_lag():
    high = read_region(region(amount=Decimal("26.60")), DAMAGED)
    assert high.verdict == "HIGH_DAY" and "26.60" in high.why
    lag = read_region(region(amount=Decimal("40")), Book(exit_low=Decimal("20"), sales=8, prior_low=Decimal("40")))
    assert lag.verdict == "LAG"
    off = read_region(region(amount=Decimal("80")), DAMAGED)
    assert off.verdict == "OFF" and "Not a profit" in off.why


def test_a_caption_that_says_sold_is_still_a_claim():
    got = read_region(region(amount=Decimal("19"), said_sold=True), DAMAGED)
    assert got.verdict == "AT_BOOK" and got.evidence_type == "SELLER_CLAIM"
    assert got.why.startswith("A caption that says sold is not a receipt.")


def test_the_number_has_to_be_on_this_card_and_the_codes_have_to_agree():
    assert read_region(region(amount_in_region=False)).status == "DETACHED"
    assert read_region(region(is_multi=True)).status == "DETACHED"
    clash = read_region(region(caption_code="LOB-EN001"))
    assert clash.status == "CONFLICT" and "LOB-001" in clash.why and "LOB-EN001" in clash.why
    assert clash.card_key is None


def test_worldwide_english_cannot_be_a_1st_and_cannot_be_product_21792():
    first = read_region(region(printed_code="LOB-EN001", variant="1st"))
    assert first.status == "CONFLICT" and "Unlimited" in first.why
    joined = read_region(region(printed_code="LOB-EN001", variant="Unlimited"))
    assert joined.status == "CONFLICT" and "not product 21792" in joined.why


def test_an_unsuffixed_collision_without_a_product_stays_quarantined():
    got = read_region(region(product_id=None))
    assert got.status == "QUARANTINED" and got.card_key is None
    named = read_region(region(product_id=None, printed_code="RDS-EN021", variant="1st", condition="NM"))
    assert named.status == "NAMED" and named.card_key == "code:RDS-EN021|1st Edition|Near Mint|English"
    assert named.verdict == ""


def test_a_slab_a_thin_book_and_a_different_currency_do_not_score():
    assert read_region(region(grade_company="PSA", grade="10")).status == "SLAB"
    thin = read_region(region(), Book(exit_low=Decimal("4000"), sales=2))
    assert thin.verdict == "NO_BOOK"
    cur = read_region(region(currency="TWD"), DAMAGED)
    assert cur.verdict == "CURRENCY" and "No conversion" in cur.why


def test_unlimited_holofoil_is_not_collapsed_and_a_name_is_not_a_printing():
    holo = read_region(region(printed_code=None, variant="Unlimited Holofoil", condition="Near Mint",
                              product_id=42382, language="en"))
    assert holo.card_key == "tcg:42382|Unlimited Holofoil|Near Mint|English"
    assert read_region(region(printed_code=None, product_id=None, variant="Holofoil")).status == "UNRESOLVED"
    assert read_region(region(printed_code="020/159", product_id=None)).status == "UNRESOLVED"


def test_a_stored_claim_is_not_a_comp(tmp_path):
    got = read_region(region(), DAMAGED)
    obs = to_observation(region(synthetic=True), got, capture_time=T)
    assert obs is not None and obs.evidence_type == EvidenceType.SELLER_CLAIM
    assert obs.printing_candidates == ("tcg:21792|Unlimited|Damaged|English",)
    st = Store(tmp_path / "s.sqlite")
    assert st.add_observation(obs, now=T) is True
    comps, rejected = select_comps(st.observations_for(obs.printing_candidates[0], as_of=T),
                                   obs.printing_candidates[0], as_of=T, include_synthetic=True)
    assert comps == [] and any("not a sale" in reason for _, reason in rejected)


def test_the_template_names_the_mirror_and_refuses_the_loose_caption(tmp_path, capsys):
    path = Path(__file__).parents[1] / "examples" / "post_region_TEMPLATE.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows[0]["synthetic"] is True and rows[1]["amount_in_region"] is False
    assert post_main([str(path), "--store", str(tmp_path / "s.sqlite")]) == 0
    out = capsys.readouterr().out
    assert "MIRROR" in out and "DETACHED" in out and "none selected as a sale" in out
