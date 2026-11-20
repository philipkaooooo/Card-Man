"""TCGplayer public payload, captured 2026-11-21. Prices here are asks and aggregates, not sales."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from cardman.tcgplayer import (
    asks_for, assemble, deepen, fetch_listings, judge, label_listing, misread, needs_cheap_page,
    page_html, parse_product, review, review_query, scan, screen, search_singles, seller_net,
    variants, worth_history)
from cardman.store import Store

DATA = Path(__file__).parent / "data" / "tcgplayer"


def load(name):
    return json.loads((DATA / name).read_text())


def test_lob001_market_price_is_not_a_1st_edition_price():
    p = parse_product(load("lob001_details.json"))
    assert p.product_id == 21792 and p.game == "yugioh" and p.number == "LOB-001"
    assert p.set_name == "The Legend of Blue Eyes White Dragon"
    assert p.in_scope and not p.sealed
    assert variants(p) == ("1st Edition", "Unlimited")
    assert len(p.skus) == 10
    assert {s.condition for s in p.skus} == {
        "Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged"}
    # One number for every SKU. Not a sale, and not Near Mint 1st.
    assert p.market_price == Decimal("119.14")
    assert p.lowest_price == Decimal("15.0")


def test_a_damaged_unlimited_ask_is_not_the_near_mint_price():
    p = parse_product(load("lob001_search_hit.json"))
    nm = asks_for(p, variant="Unlimited", condition="Near Mint")
    dmg = asks_for(p, variant="Unlimited", condition="Damaged")
    assert [a.price for a in nm] == [Decimal("161.99")]
    assert min(a.price for a in dmg) == Decimal("19.87")
    assert asks_for(p, variant="1st Edition") == ()
    assert p.market_price != nm[0].price


def test_charizard_is_pokemon_holofoil_not_yugioh():
    p = parse_product(load("charizard_base_details.json"))
    assert p.game == "pokemon" and p.number == "004/102" and p.set_name == "Base Set"
    assert variants(p) == ("Holofoil",)
    assert p.market_price == Decimal("1001.05")
    assert p.in_scope


def test_sealed_other_games_and_presale_are_out_of_scope():
    base = load("lob001_details.json")
    sealed = dict(base, sealed=True)
    assert parse_product(sealed).in_scope is False
    magic = dict(base, productLineId=1)
    assert parse_product(magic) is None
    presale = dict(base, customAttributes={"number": "LOB-001", "detailNote": "This product is a Presale item."})
    assert parse_product(presale).presale is True and parse_product(presale).in_scope is False
    japan = dict(base, productLineId=85, productLineName="Pokemon Japan")
    assert parse_product(japan).game == "pokemon_japan"


def test_the_market_gap_on_lob001_is_a_misread_and_the_same_sku_gap_dies():
    p = parse_product(load("lob001_search_hit.json"))
    text = misread(p)
    assert text is not None and "not a return" in text and "Damaged" in text
    rows = { (r.printing, r.condition): r for r in screen(p) }
    damaged = rows[("Unlimited", "Damaged")]
    assert damaged.label == "NO" and damaged.after_fees < 0
    assert damaged.buy == Decimal("19.87") and damaged.exit_ask == Decimal("19.90")
    # one Near Mint ask: nothing to undercut
    assert rows[("Unlimited", "Near Mint")].label == "ABSTAIN"


def test_a_same_sku_gap_that_survives_fees_is_a_look_not_a_return():
    hit = load("lob001_search_hit.json")
    hit["listings"] = [
        {"listingId": 1, "productId": 21792, "price": 40, "shippingPrice": 0,
         "condition": "Near Mint", "printing": "1st Edition", "language": "English", "quantity": 1},
        {"listingId": 2, "productId": 21792, "price": 80, "shippingPrice": 0,
         "condition": "Near Mint", "printing": "1st Edition", "language": "English", "quantity": 1},
    ]
    row = screen(parse_product(hit))[0]
    assert row.label == "LOOK" and row.after_fees > 0 and "not a settled sale" in row.why


def test_commission_caps_at_75_dollars():
    # 10.75% of 1000 is 107.50. The cap keeps 75. Then 2.5% is 25, plus 0.30.
    assert seller_net(Decimal("1000")) == Decimal("899.70")
    assert seller_net(Decimal("100")) == Decimal("86.45")


def test_near_mint_unlimited_ask_is_above_the_median_cleared_low():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[0]
    assert listing.condition == "Near Mint" and listing.printing == "Unlimited"
    row = judge(listing, load("lob001_quarter.json"))
    assert row.label == "NO" and row.sales == 10
    # 108.95 is in the book. The median low is still 119.99, and the ask is above it.
    assert row.exit_low == Decimal("119.99") and row.exit_date == "2026-11-14"
    assert row.direction == "UP" and row.prior_low == Decimal("108.98") and row.recent_low == Decimal("122.98")
    assert "108.95" in row.why and row.after_fees < 0


def test_one_high_damaged_day_does_not_set_the_exit():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[1]
    assert listing.condition == "Damaged" and listing.price == Decimal("19.87")
    row = judge(listing, load("lob001_quarter.json"))
    # Five of the ten sales cleared at 19. The 26.60 day is real and is not the exit.
    assert row.label == "NO" and row.exit_low == Decimal("19") and row.sales == 10
    assert row.direction == "UP" and row.prior_low == Decimal("19") and row.recent_low == Decimal("19.96")
    assert row.after_fees == Decimal("-3.69")
    assert "26.6" in row.why


def test_a_falling_book_is_priced_on_the_recent_half_not_the_old_highs():
    listing = parse_product(load("lob001_search_hit.json")).listings[1]
    listing = type(listing)(**{**listing.__dict__, "price": Decimal("25"), "shipping": Decimal("0"),
                               "condition": "Near Mint", "printing": "Unlimited"})
    history = {"result": [{"skuId": "1", "variant": "Unlimited", "condition": "Near Mint", "language": "English",
                           "buckets": [
                               {"bucketStartDate": "2026-08-01", "transactionCount": "5", "lowSalePrice": "40",
                                "highSalePrice": "40", "marketPrice": "40"},
                               {"bucketStartDate": "2026-11-01", "transactionCount": "3", "lowSalePrice": "20",
                                "highSalePrice": "20", "marketPrice": "20"},
                           ]}]}
    row = judge(listing, history)
    # The whole-window median is 40. The newer half is 20. Paying 40 would call a 25 ask a look.
    assert row.direction == "DOWN" and row.prior_low == Decimal("40") and row.recent_low == Decimal("20")
    assert row.exit_low == Decimal("20") and row.label == "NO" and row.after_fees == Decimal("-7.95")


def test_the_chart_holds_the_same_numbers_as_the_table():
    hit = load("lob001_search_hit.json")
    page = page_html(review([parse_product(hit)], {21792: load("lob001_quarter.json")}))
    assert "<svg" in page and "2026-11-14" in page and ">119.99<" in page
    assert ">19<" in page and "165.98" in page and "19.87" in page


def test_the_median_low_is_the_exit_when_one_bucket_is_high():
    listing = parse_product(load("lob001_search_hit.json")).listings[1]
    listing = type(listing)(**{**listing.__dict__, "price": Decimal("10"), "shipping": Decimal("0"),
                               "condition": "Near Mint", "printing": "Unlimited"})
    history = {"result": [{"skuId": "1", "variant": "Unlimited", "condition": "Near Mint", "language": "English",
                           "buckets": [
                               {"bucketStartDate": "2026-11-01", "transactionCount": "1", "lowSalePrice": "40",
                                "highSalePrice": "40", "marketPrice": "40"},
                               {"bucketStartDate": "2026-11-02", "transactionCount": "1", "lowSalePrice": "40",
                                "highSalePrice": "40", "marketPrice": "40"},
                               {"bucketStartDate": "2026-11-03", "transactionCount": "1", "lowSalePrice": "100",
                                "highSalePrice": "100", "marketPrice": "40"},
                           ]}]}
    row = judge(listing, history)
    assert row.label == "LOOK" and row.exit_low == Decimal("40") and row.sales == 3
    assert row.after_fees == Decimal("24.40")
    assert "not a return" in row.why.lower()


def test_two_1st_edition_prints_a_year_apart_are_not_a_book():
    hit = load("lob001_search_hit.json")
    # Same dollars as the Near Mint Unlimited ask, but the SKU is 1st Edition.
    listing = parse_product(hit).listings[0]
    listing = type(listing)(**{**listing.__dict__, "printing": "1st Edition"})
    row = judge(listing, load("lob001_1st_nm_annual.json"))
    assert row.label == "THIN" and row.sales == 1 and row.after_fees is None
    assert "14.99" in row.why and "Not a book" in row.why
    assert "Rejected" in row.why


def test_a_1st_edition_ask_does_not_use_unlimited_sales():
    hit = load("lob001_search_hit.json")
    listing = parse_product(hit).listings[0]
    listing = type(listing)(**{**listing.__dict__, "printing": "1st Edition"})
    row = judge(listing, load("lob001_quarter.json"))
    assert row.label == "ABSTAIN" and row.sales == 0
    hit = load("lob001_search_hit.json")
    sealed = dict(hit, productId=1, sealed=True, listings=[])
    magic = dict(hit, productId=2, productLineId=1, listings=[])
    payload = {"results": [{"results": [hit, sealed, magic]}]}

    def post(url, body, timeout):
        assert "yugioh" in url or body["filters"]["term"]["productLineName"] == ["yugioh"]
        return payload

    got = search_singles("yugioh", "Blue-Eyes White Dragon", post=post)
    assert [p.product_id for p in got] == [21792]
    with pytest.raises(ValueError):
        search_singles("magic", "x", post=post)


def test_a_page_of_presales_is_skipped_for_the_next_in_scope_single():
    hit = load("lob001_search_hit.json")
    note = {"number": "MAMS-EN001", "detailNote": "This product is a Presale item."}
    presales = [dict(hit, productId=100 + i, listings=[], customAttributes=note) for i in range(10)]
    pages = [{"results": [{"results": presales}]}, {"results": [{"results": [hit]}]}]

    def post(url, body, timeout):
        assert body["size"] == 10
        return pages.pop(0)

    got = search_singles("yugioh", "Blue-Eyes", size=1, post=post)
    assert [p.product_id for p in got] == [21792] and pages == []
    hit = dict(load("lob001_search_hit.json"), totalListings=151)
    product = parse_product(hit)
    assert product.listed == 151 and len(product.listings) == 3
    row = review([product], {21792: load("lob001_quarter.json")})[0]
    assert row.judged == 3 and row.listed == 151 and "not a return" in row.misread
    assert {(r.condition, r.label) for r in row.reads} == {("Near Mint", "NO"), ("Damaged", "NO")}


def test_a_failed_history_abstains_and_is_not_fetched_twice():
    hit = load("lob001_search_hit.json")
    payload = {"results": [{"results": [hit, hit]}]}

    def search(game, query, size=10):
        return search_singles(game, query, size=size, post=lambda url, body, timeout: payload)

    calls = []

    def history(pid, span):
        calls.append((pid, span))
        raise TimeoutError("down")

    rows = review_query("yugioh", "Blue-Eyes", search=search, history=history)
    assert calls == [(21792, "quarter")] and len(rows) == 2
    assert rows[0].history_note == "history failed: TimeoutError"
    assert {r.label for r in rows[0].reads} == {"ABSTAIN"}
    assert {c.label for c in rows[0].calls} == {"watch"}


def _priced(**kw):
    hit = load("lob001_details.json")
    row = {"listingId": kw.get("listing_id", 1), "productId": kw.get("product_id", 21792),
           "price": kw.get("price", "10"), "condition": kw.get("condition", "Near Mint"),
           "printing": kw.get("printing", "Unlimited"), "language": kw.get("language", "English"), "quantity": 1}
    if kw.get("shipping", 0) is not None:
        row["shippingPrice"] = kw.get("shipping", 0)
    hit["listings"] = [row]
    return parse_product(hit)


def _clears(low="100", n=3):
    buckets = [{"bucketStartDate": f"2026-11-0{i}", "transactionCount": "1", "lowSalePrice": low,
                "highSalePrice": low, "marketPrice": low} for i in range(1, n + 1)]
    return {"result": [{"skuId": "1", "variant": "Unlimited", "condition": "Near Mint", "language": "English",
                        "buckets": buckets}]}


def test_a_double_after_fees_is_2x_and_a_smaller_gap_is_buy():
    product = _priced(price="10", shipping=0)
    double = label_listing(product, product.listings[0], _clears("100"))
    assert double.label == "2x" and double.multiple >= Decimal("2") and double.typed
    product = _priced(price="50", shipping=0)
    bought = label_listing(product, product.listings[0], _clears("100"))
    assert bought.label == "buy" and bought.multiple < Decimal("2") and bought.after_fees > 0


def test_blank_shipping_or_a_thin_book_is_watch_and_an_expensive_ask_is_pass():
    product = _priced(price="10", shipping=None)
    assert label_listing(product, product.listings[0], _clears("100")).label == "watch"
    product = _priced(price="10", shipping=0)
    assert label_listing(product, product.listings[0], _clears("100", n=2)).label == "watch"
    assert label_listing(product, product.listings[0], _clears("100")).label == "2x"
    product = _priced(price="90", shipping=0)
    assert label_listing(product, product.listings[0], _clears("100")).label == "pass"


def test_a_blank_type_is_not_stored_and_a_listing_cannot_change_card(tmp_path):
    product = _priced(printing="")
    blank = label_listing(product, product.listings[0], _clears())
    assert blank.typed is False
    product = _priced(price="10", shipping=0)
    call = label_listing(product, product.listings[0], _clears())
    from datetime import datetime, timezone
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    st = Store(tmp_path / "s.sqlite")
    written, refused = st.save_public_listings([call.row()], now)
    assert written == 1 and refused == []
    moved = dict(call.row(), printing="1st Edition", card_key="tcg:21792|1st Edition|Near Mint|English")
    written, refused = st.save_public_listings([moved], now)
    assert written == 0 and "not tcg:21792|1st Edition" in refused[0]
    assert st.db.execute("SELECT printing, label FROM public_listing").fetchone() == ("Unlimited", "2x")


def test_paging_keeps_this_product_only_and_a_count_is_not_asks():
    pages = {
        0: {"results": [{"totalResults": 3, "results": [
            {"listingId": 1, "productId": 21792, "price": 1},
            {"listingId": 2, "productId": 999, "price": 2},
        ]}]},
        2: {"results": [{"totalResults": 3, "results": [
            {"listingId": 3, "productId": 21792, "price": 3},
        ]}]},
    }

    def post(url, body):
        assert str(21792) in url
        return pages[body["from"]]

    rows, total = fetch_listings(21792, post=post, page_size=2)
    assert total == 3 and [r["listingId"] for r in rows] == [1, 3]
    details = load("lob001_details.json")
    details["listings"] = 115
    product = assemble(details, [{
        "listingId": 7, "productId": 21792, "price": 10, "shippingPrice": 1,
        "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1,
    }], 115)
    assert product.listed == 115 and len(product.listings) == 1
    assert label_listing(product, product.listings[0], None).label == "watch"


def _at_market(hit, market, listings, lowest=None, listed=None):
    doc = dict(hit, marketPrice=market, listings=listings,
               totalListings=len(listings) if listed is None else listed)
    if lowest is not None:
        doc["lowestPrice"] = lowest
    return parse_product(doc)


def test_an_ask_that_cannot_clear_fees_at_market_skips_history():
    hit = load("lob001_search_hit.json")
    # Market 10. seller_net is about 8.38. An ask of 9 cannot win even at that market.
    hopeless = _at_market(hit, 10, [{"listingId": 1, "productId": 21792, "price": 9, "shippingPrice": 0,
                                     "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1}])
    cheap = _at_market(hit, 10, [{"listingId": 2, "productId": 50, "price": 2, "shippingPrice": 0,
                                  "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1}])
    cheap = type(cheap)(**{**cheap.__dict__, "product_id": 50})
    assert worth_history(hopeless) is False and worth_history(cheap) is True
    assert needs_cheap_page(hopeless) is False and needs_cheap_page(cheap) is False
    calls = []

    def search(game, query, size=10, sort=None):
        assert sort == ("sales", "desc")
        return [hopeless, cheap]

    def fetch(pid, span):
        calls.append(pid)
        return {"result": []}

    result = scan("yugioh", "", size=2, cache_dir=None, search=search, fetch=fetch, cheap=lambda p: p)
    assert calls == [50] and result.checked == 2 and result.fetched == 1 and result.skipped == 1
    assert result.looks == () and result.deepened == 0


def test_a_second_scan_uses_the_cached_book(tmp_path):
    hit = load("lob001_search_hit.json")
    product = parse_product(hit)
    calls = []

    def search(game, query, size=10, sort=None):
        return [product]

    def fetch(pid, span):
        calls.append(pid)
        return load("lob001_quarter.json")

    first = scan("yugioh", "LOB-001", cache_dir=str(tmp_path), search=search, fetch=fetch, cheap=lambda p: p)
    second = scan("yugioh", "LOB-001", cache_dir=str(tmp_path), search=search, fetch=fetch, cheap=lambda p: p)
    assert calls == [21792] and second.cache_hits == 1 and second.fetched == 0
    assert first.closest and first.closest[0].read.label == "NO"


def test_a_floor_missing_from_the_sample_is_priced_off_the_cheap_page():
    hit = load("lob001_search_hit.json")
    sample = _at_market(hit, 100, [{
        "listingId": 1, "productId": 21792, "price": 90, "shippingPrice": 0,
        "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1,
    }], lowest=10, listed=40)
    assert needs_cheap_page(sample)
    floor = {
        "listingId": 9, "productId": 21792, "price": 10, "shippingPrice": 0,
        "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1,
    }

    def fetch(pid, span):
        return {"result": [{"skuId": "1", "variant": "Unlimited", "condition": "Near Mint",
                            "language": "English", "buckets": [
                                {"bucketStartDate": f"2026-11-0{i}", "transactionCount": "1",
                                 "lowSalePrice": "40", "highSalePrice": "40", "marketPrice": "40"}
                                for i in range(1, 4)]}]}

    result = scan("yugioh", "x", cache_dir=None, search=lambda *a, **k: [sample], fetch=fetch,
                  cheap=lambda product: _at_market(hit, 100, [floor], lowest=10, listed=40))
    assert result.deepened == 1 and result.fetched == 1
    assert result.looks[0].read.buy == Decimal("10") and result.looks[0].decision == "2x"
    assert "Not a return" in result.looks[0].read.why


def test_shipping_that_kills_the_floor_skips_the_book():
    hit = load("lob001_search_hit.json")
    sample = _at_market(hit, 10, [{
        "listingId": 1, "productId": 21792, "price": 9, "shippingPrice": 0,
        "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1,
    }], lowest=5, listed=20)
    assert worth_history(sample) is True
    calls = []

    def cheap(product):
        return _at_market(hit, 10, [{
            "listingId": 4, "productId": 21792, "price": 5, "shippingPrice": 6,
            "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1,
        }], lowest=5, listed=20)

    result = scan("yugioh", "x", cache_dir=None, search=lambda *a, **k: [sample],
                  fetch=lambda pid, span: calls.append(pid) or {"result": []}, cheap=cheap)
    assert calls == [] and result.fetched == 0 and result.deepened == 1 and result.skipped == 1


def test_near_mint_is_added_when_the_cheap_page_is_all_played():
    hit = load("lob001_search_hit.json")
    sample = _at_market(hit, 100, [{
        "listingId": 1, "productId": 21792, "price": 90, "shippingPrice": 0,
        "condition": "Damaged", "printing": "Unlimited", "language": "English", "quantity": 1,
    }], lowest=10, listed=80)
    damaged = [{"listingId": 2, "productId": 21792, "price": 10, "shippingPrice": 0,
                "condition": "Damaged", "printing": "Unlimited", "language": "English", "quantity": 1}]
    nm = [{"listingId": 3, "productId": 21792, "price": 30, "shippingPrice": 0,
           "condition": "Near Mint", "printing": "Unlimited", "language": "English", "quantity": 1}]

    def post(url, body, timeout=25):
        rows = nm if "condition" in body["filters"]["term"] else damaged
        assert body["size"] == 50 and body["from"] == 0
        return {"results": [{"totalResults": 80, "results": rows}]}

    out = deepen(sample, post=post)
    assert {row.condition for row in out.listings} == {"Damaged", "Near Mint"}
    assert out.listed == 80
