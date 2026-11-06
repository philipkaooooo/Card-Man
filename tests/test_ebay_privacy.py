from cardman import ebay_privacy as ep


def test_seller_token_is_stable_and_not_the_name(monkeypatch):
    monkeypatch.setenv("CARDMAN_PRIVACY_SALT", "test-salt")
    t = ep.seller_token("CardShop_99")
    assert t.startswith("seller:") and "CardShop" not in t
    assert ep.seller_token("cardshop_99") == t
    assert ep.seller_token(t) == t
    assert ep.seller_token(None) is None


def test_strip_user_fields_keeps_prices():
    page = {"itemSummaries": [{"itemId": "1", "price": {"value": "5"}, "seller": {"username": "x"}}]}
    out = ep.strip_user_fields(page)
    assert out["itemSummaries"][0] == {"itemId": "1", "price": {"value": "5"}}
