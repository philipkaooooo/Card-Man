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
    assert v.code == BUY and v.headline == "Buy up to $500" and "6 recent sales" in v.reason and v.confidence == "High"


def test_no_real_sales_never_buy_whatever_else_says_so():
    for d in (D(kinds=["UNDERPRICED", "REFERENCE_GAP"], scenarios=[sc(basis="reference")], printing_id="p:x@1"),
              D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")]),
              D(kinds=["UNDERPRICED"], scenarios=[sc(basis="none")], printing_id="p:x@1")):
        assert verdict(d, "OPEN", M).code != BUY


def test_cheap_only_if_its_the_right_print_needs_a_check():
    d = D(kinds=["RIGHT_PRINT_ONLY"], scenarios=[sc(weight=0.4), sc(basis="none", weight=0.6, pid="p:y@1")], priced_share=0.4)
    v = verdict(d, "OPEN", M)
    assert v.code == CHECK and "2 printings" in v.reason and v.confidence == "Low"


def test_over_max_buy_everywhere_is_a_pass():
    d = D(kinds=[], scenarios=[sc(max_buy=300.0)], priced_share=1.0, printing_id="p:x@1", best_profit=-50.0)
    assert verdict(d, "OPEN", M).code == PASS


def test_guide_only_is_watch_with_the_reason_said_plainly():
    v = verdict(D(kinds=["REFERENCE_GAP"], scenarios=[sc(basis="reference")]), "OPEN", M)
    assert v.code == WATCH and v.reason == "No sales yet: the price is a guide estimate only."


def test_lots_say_what_they_are():
    v = verdict(D(title="Yu-Gi-Oh! Collection Lot 250+ Cards", kinds=["LOT", "COLLECTION"]), "OPEN", M)
    assert v.code == WATCH and v.reason.startswith("Lot of 250: can't value it per card")
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
    assert "Watch" in outside and "guide estimate only" in outside
    assert key not in outside and "SPECTATE" not in outside and "fp" not in outside and "evpi" not in outside.lower()
    assert key in page and re.search(r"<details class=\"dsec\"><summary>Raw listing data", page) and "<details class=\"dsec\" open" not in page
