"""The TCGplayer ask feed: one cheapest Near Mint page per version, its own variant only, stored as asks."""
from cardman import tcgplayer as tcg

PID = "pk:EN:cubone1-1:unlimited@pkm-cubone1-1"


def _rows():
    return [
        {"listingId": 1, "productId": 42416, "price": 5.0, "shippingPrice": 1.0, "condition": "Near Mint",
         "printing": "Normal", "language": "English", "quantity": 1},
        {"listingId": 2, "productId": 42416, "price": 4.0, "shippingPrice": 0.0, "condition": "Near Mint",
         "printing": "Holofoil", "language": "English", "quantity": 1},
    ]


def test_feed_keeps_only_the_products_own_variant_and_near_mint(tmp_path, monkeypatch):
    seen = []

    def fake_fetch(product_id, **kw):
        seen.append((product_id, kw.get("term")))
        return _rows(), 2

    monkeypatch.setattr(tcg, "_feed_jobs", lambda dex_max, canonical_only: [(42416, PID, "Normal", "Cubone", "68")])
    monkeypatch.setattr(tcg, "fetch_listings", fake_fetch)
    tcg.feed(str(tmp_path / "s.sqlite"), log=lambda *a: None)
    con = __import__("sqlite3").connect(tmp_path / "s.sqlite")
    got = con.execute("SELECT listing_id, product_id, printing, condition, price, shipping "
                      "FROM public_listing").fetchall()
    assert got == [("1", "42416", "Normal", "Near Mint", "5.0", "1.0")]
    assert seen[0] == (42416, {"condition": ["Near Mint"], "language": ["English"]})


def test_a_product_without_near_mint_falls_back_to_lightly_played(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(product_id, **kw):
        calls.append(kw["term"]["condition"][0])
        if kw["term"]["condition"][0] == "Near Mint":
            return [], 0
        return [{"listingId": 3, "productId": 42416, "price": 4.5, "shippingPrice": 0.0,
                 "condition": "Lightly Played", "printing": "Normal", "language": "English", "quantity": 1}], 1

    monkeypatch.setattr(tcg, "_feed_jobs", lambda dex_max, canonical_only: [(42416, PID, "Normal", "Cubone", "68")])
    monkeypatch.setattr(tcg, "fetch_listings", fake_fetch)
    tcg.feed(str(tmp_path / "s.sqlite"), log=lambda *a: None)
    con = __import__("sqlite3").connect(tmp_path / "s.sqlite")
    assert con.execute("SELECT condition FROM public_listing").fetchall() == [("Lightly Played",)]
    assert calls == ["Near Mint", "Lightly Played"]


def test_a_failed_product_does_not_stop_the_feed(tmp_path, monkeypatch):
    def fake_fetch(product_id, **kw):
        raise TimeoutError("slow")

    monkeypatch.setattr(tcg, "_feed_jobs", lambda dex_max, canonical_only: [(42416, PID, "Normal", "Cubone", "68")])
    monkeypatch.setattr(tcg, "fetch_listings", fake_fetch)
    stats = tcg.feed(str(tmp_path / "s.sqlite"), log=lambda *a: None)
    assert stats["failed"] == 1 and stats["written"] == 0
