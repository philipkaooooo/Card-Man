"""Each listing reads as one decision; the verdict only restates what the engine computed."""
from cardman.ui_listing import BUY, CHECK, PASS, WATCH, verdict


def M(v, sign=False):
    return "—" if v is None else f"${float(v):,.0f}"


def sc(basis="sales", weight=1.0, max_buy=500.0, n=6, label="LOB-EN001 · Ultra Rare · 1st", pid="p:x@89631139", mid=900.0):
    return {"printing_id": pid, "label": label, "weight": weight, "basis": basis, "n_sales": n if basis == "sales" else 0,
            "resale_low": 700.0, "resale_mid": mid, "profit_mid": 100.0, "max_buy": max_buy if basis == "sales" else None}


def D(**kw):
    base = {"title": "Blue-Eyes White Dragon LOB-EN001", "kinds": [], "scenarios": [], "priced_share": 0.0, "all_in_home": 400.0,
            "ask_home": 380.0, "printing_id": None, "findings": [], "reasons": [], "expected_profit": None, "best_profit": None}
    base.update(kw)
    return base


def test_buy_needs_real_sales_a_settled_identity_and_an_all_in_under_max_buy():
    v = verdict(D(kinds=["UNDERPRICED"], scenarios=[sc()], priced_share=1.0, printing_id="p:x@89631139"), "ESCALATED", M)
    assert v.code == BUY and v.headline == "Buy (up to $500)" and "6 recent sales" in v.reason and v.confidence == "High"


def test_no_real_sales_never_buy_whatever_else_says_so():
    for d in (D(kinds=["UNDERPRICED", "REFERENCE_GAP"], scenarios=[sc(basis="reference")], printing_id="p:x@1"),
              D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")]),
              D(kinds=["UNDERPRICED"], scenarios=[sc(basis="none")], printing_id="p:x@1")):
        assert verdict(d, "OPEN", M).code != BUY


def test_cheap_only_if_its_the_right_print_needs_a_check():
    d = D(kinds=["RIGHT_PRINT_ONLY"], scenarios=[sc(weight=0.4), sc(basis="none", weight=0.6, pid="p:y@1")], priced_share=0.4)
    v = verdict(d, "OPEN", M)
    assert v.code == CHECK and "one of 2 versions" in v.reason and "Check the photos" in v.reason and v.confidence == "Low"


def test_over_max_buy_everywhere_is_a_pass():
    d = D(kinds=[], scenarios=[sc(max_buy=300.0)], priced_share=1.0, printing_id="p:x@1", best_profit=-50.0)
    assert verdict(d, "OPEN", M).code == PASS


def test_a_price_guide_is_not_what_the_card_sells_for():
    v = verdict(D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")]), "OPEN", M)
    assert v.code == CHECK and v.top is None
    assert "price guide is not a sale" in v.reason and "usually" not in v.reason.lower()


def test_lots_say_what_they_are():
    v = verdict(D(title="Yu-Gi-Oh! Collection Lot 250+ Cards", kinds=["LOT", "COLLECTION"]), "OPEN", M)
    assert v.code == WATCH and v.reason.startswith("A lot of 250 cards: we can't tell what each card is worth yet")
    assert verdict(D(title="Lot of 40 cards", kinds=["LOT"]), "SHELVED", M).code == PASS


def test_rejected_listings_pass_with_the_blocking_reason():
    v = verdict(D(findings=[{"kind": "COUNTERFEIT_CUE", "severity": "BLOCK", "text": "Listing text suggests a non-genuine card: proxy"}]),
                "REJECTED", M)
    assert v.code == PASS and "proxy" in v.reason


def test_misidentification_is_a_check_with_the_finding():
    d = D(kinds=["MISIDENTIFIED"], findings=[{"kind": "CODE_NAME_MISMATCH", "severity": "WARN", "text": "LOB-EN005 belongs to Dark Magician"}])
    v = verdict(d, "OPEN", M)
    assert v.code == CHECK and "Dark Magician" in v.reason


def test_a_set_prefix_written_alone_favours_that_product():
    from cardman.intel import analyze
    from tests.test_engine import CAT
    a = analyze("Blue-Eyes White Dragon LOB 1st Edition Ultra Rare", CAT)
    top = max(a.identity.candidates, key=lambda p: a.weights[p.id])
    assert top.set_code.startswith("LOB")


def test_listing_page_keeps_raw_data_inside_closed_details(tmp_path):
    import json
    import re
    from datetime import datetime, timezone
    from cardman.ui_listing import view_one
    from cardman.store import Store
    db = Store(tmp_path / "s.sqlite")
    db.db.executescript("CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
                        "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);"
                        "CREATE TABLE listing (key TEXT PRIMARY KEY, source TEXT, listing_id TEXT, market TEXT, title TEXT, url TEXT, seller TEXT, kind TEXT, "
                        "price TEXT, currency TEXT, ends_at TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, fingerprint TEXT, data_json TEXT);")
    key = "ebay_browse:EBAY_US:123456789012"
    d = D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")], url="https://www.ebay.com/itm/123456789012", market="INTL",
          evpi=12.5, research=[{"what": "Confirm: close-up of the printed set code", "minutes": 3}])
    with db.db:
        db.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (key, "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp",
                                                                                 json.dumps(d), "t", "t"))
        db.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (key, "ebay_browse:EBAY_US", "123456789012", "INTL", d["title"],
                                                                                    d["url"], None, "ask", "12", "USD", None, "t", "t", None, "fp", "{}"))
    page = view_one(db, None, key, datetime.now(timezone.utc), "tok", M, None)
    outside = re.sub(r"<details.*?</details>", "", page, flags=re.S)
    assert "2x Check" in outside and "not a sale" in outside
    assert key not in outside and "SPECTATE" not in outside and "fp" not in outside and "evpi" not in outside.lower()
    assert key in page and re.search(r"<details class=\"dsec\"><summary>Technical details", page) and "<details class=\"dsec\" open" not in page


def test_listing_filters_site_kind_and_price(tmp_path):
    import json
    from datetime import datetime, timezone
    from cardman.store import Store
    from cardman.ui_listing import view_list
    st = Store(tmp_path / "s.sqlite")
    st.db.execute("CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
                  "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT)")
    rows = [("ebay_browse:EBAY_US:1", D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")], all_in_home=100.0, title="Cheap eBay card")),
            ("yahoo_auctions_jp:x1", D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")], all_in_home=900.0, title="Dear Yahoo card")),
            ("ebay_browse:EBAY_US:2", D(kinds=["LOT", "COLLECTION"], title="Lot of 300 cards", all_in_home=300.0))]
    with st.db:
        for k, d in rows:
            st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (k, "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp", json.dumps(d), "t", "t"))
    now = datetime.now(timezone.utc)
    page = lambda **kw: view_list(st, None, now, M, v_filter="all", t_filter=kw.pop("t", "any"), home_cur="USD", **kw)
    assert "Cheap eBay card" in page() and "Dear Yahoo card" in page() and "Lot of 300" in page()
    assert "Dear Yahoo card" not in page(site="ebay") and "Cheap eBay card" not in page(site="yahoo")
    assert "Lot of 300" not in page(t="singles") and "Cheap eBay card" not in page(t="lots")
    assert 'class="filterbar"' in page() and 'name="min"' not in page() and "Total from" not in page()   # no price range
    p = page()
    assert '<nav class="rail"' in p and all(w in p for w in ("Buy", "2x Check", "Watch", "Skip"))
    assert '<h3>All Cards</h3>' in p and '<h3>Sites</h3>' in p


def test_reprints_and_impossible_prices_are_never_watch_or_confirmed():
    from cardman.ui_listing import listing_icons, verdict, suspect
    from datetime import datetime, timezone
    celebi = D(kinds=["REFERENCE_GAP"], title="Shining Celebi 106/105 Neo Revelation 1st Edition Holo Rare 30th Anniversary",
               all_in_home=1502.0, findings=[{"kind": "ASPECTS_CONFIRM", "severity": "INFO", "text": "eBay's item details confirm this version"}],
               scenarios=[sc(basis="reference", label="Neo Destiny 106/105 · Rare Shining · 1st Edition Holo", mid=59288.0)])
    v = verdict(celebi, "OPEN", M)
    assert v.code == PASS and "30th" in v.reason and "looks cheap" not in v.reason.lower()
    assert "confirm" not in listing_icons(celebi, datetime(2026, 11, 19, tzinfo=timezone.utc))
    cheap = D(kinds=["REFERENCE_GAP"], title="Charizard 4/102 Base Set Holo", all_in_home=700.0,
              scenarios=[sc(basis="reference", label="Base 4/102 · Rare Holo · Unlimited Holo", mid=31472.0)])
    assert verdict(cheap, "OPEN", M).code == PASS and "2%" in verdict(cheap, "OPEN", M).reason
    fine = D(kinds=["REFERENCE_GAP"], title="Shining Celebi 106 30th Celebration Classic Collection", all_in_home=1500.0,
             scenarios=[sc(basis="reference", label="30th Celebration: Classic Collection 106 · Rare Secret · Holo", mid=1700.0)])
    assert suspect(fine) is None
    from cardman.aspect_identity import unsettled
    assert unsettled("Shining Celebi 106/105 Neo Revelation 1st Edition 30th Anniversary", {}) == "30th"


def test_a_guide_price_at_a_fraction_of_the_ask_is_not_a_watch():
    d = D(kinds=["REFERENCE_GAP"], all_in_home=28.34, ask_home=10.0,
          title="The Pokemon Company Mewtwo GG44/GG70 Crown Zenith Full Art Holo Rare Eng",
          scenarios=[sc(basis="reference", mid=237.0,
                        label="Crown Zenith Galarian Gallery GG44/70 · Rare Holo VSTAR · Holo",
                        pid="pk:EN:swsh12pt5gg-GG44:holo@pkm-swsh12pt5gg-GG44")])
    v = verdict(d, "OPEN", M)
    assert v.code == PASS and v.top is None and "not a bargain" in v.reason and "looks cheap" not in v.reason.lower()


def test_a_different_photo_is_not_the_named_card():
    import struct
    import zlib
    from cardman.photo_match import disagrees

    def png(rgb):
        raw = b"".join(b"\x00" + bytes(rgb) * 16 for _ in range(16))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    assert disagrees(png((250, 220, 40)), png((30, 10, 40))) is True
    assert disagrees(png((250, 220, 40)), png((248, 218, 42))) is False
    assert disagrees(b"not an image", png((1, 2, 3))) is None


def test_a_matching_card_on_a_colored_background_is_the_card_and_the_swap_is_not():
    import struct
    import zlib
    from cardman.photo_match import judge

    def png(w, h, paint):
        raw = b"".join(b"\x00" + b"".join(bytes(paint(x, y)) for x in range(w)) for y in range(h))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    yellow, purple, green = (250, 220, 40), (30, 10, 40), (20, 140, 40)
    card = png(40, 56, lambda x, y: yellow if x < 20 else purple)

    def framed(flipped):
        def paint(x, y):
            if 25 <= x < 65 and 7 <= y < 63:
                left = purple if flipped else yellow
                right = yellow if flipped else purple
                return left if (x - 25) < 20 else right
            return green
        return png(90, 70, paint)

    assert judge(framed(False), card) == "match"
    assert judge(framed(True), card) == "clash"


def test_any_listing_with_a_different_photo_loses_the_card_and_its_price(tmp_path):
    from cardman.listing_detail import cache_key
    from cardman.photo_match import apply

    def png(rgb):
        import struct
        import zlib
        raw = b"".join(b"\x00" + bytes(rgb) * 16 for _ in range(16))

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    iid = "1218199929468"
    k = cache_key(iid, 0, "s")
    listing = tmp_path / "listing"
    (listing / k[:2]).mkdir(parents=True)
    (listing / k[:2] / f"{k}.img").write_bytes(png((30, 10, 40)))
    images = tmp_path / "images"
    (images / "pkm" / "small").mkdir(parents=True)
    (images / "pkm" / "small" / "swsh12pt5gg-GG44.png").write_bytes(png((250, 220, 40)))
    raw = D(kinds=["REFERENCE_GAP"], printing_id="pk:EN:swsh12pt5gg-GG44:holo@pkm-swsh12pt5gg-GG44", all_in_home=28.34,
            scenarios=[sc(basis="reference", mid=237.0, pid="pk:EN:swsh12pt5gg-GG44:holo@pkm-swsh12pt5gg-GG44")])
    out = apply(raw, f"ebay_browse:EBAY_US:{iid}", listing_root=listing, image_root=images)
    assert out["photo"] == "clash" and out["printing_id"] is None
    assert all(s["basis"] == "none" and s["resale_mid"] is None for s in out["scenarios"])
    v = verdict(out, "OPEN", M)
    assert v.code == PASS and v.top is None and "photo" in v.reason.lower()

    (listing / k[:2] / f"{k}.img").write_bytes(png((250, 220, 40)))
    matched = apply(raw, f"ebay_browse:EBAY_US:{iid}", listing_root=listing, image_root=images)
    assert matched["photo"] == "match" and matched["printing_id"]
    assert apply(raw, "yahoo_auctions_jp:x")["photo"] == "none"


