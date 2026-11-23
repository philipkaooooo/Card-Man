"""Card Pile: what a person set aside, and the page that prices it from its own sales."""
import json
from datetime import datetime, timezone

from cardman.pile import add, chase_add, chases, keys, remove, resolve, set_side, sort, view
from cardman.store import Store

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)
KEY = "ebay_browse:EBAY_US:123456789012"


class M:
    def __init__(self, now=NOW, home="USD"):
        self.now, self.home = now, home

    def __call__(self, v, sign=False):
        return "—" if v is None else f"${v:,.0f}"


def seed(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    st.db.executescript(
        "CREATE TABLE opportunity (key TEXT PRIMARY KEY, stage TEXT, status TEXT, priority REAL, expected REAL, evpi REAL, kinds TEXT, "
        "printing_id TEXT, snapshot_id TEXT, analysis_fp TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE listing (key TEXT PRIMARY KEY, source TEXT, listing_id TEXT, market TEXT, title TEXT, url TEXT, seller TEXT, kind TEXT, "
        "price TEXT, currency TEXT, ends_at TEXT, first_seen TEXT, last_seen TEXT, ended_at TEXT, fingerprint TEXT, data_json TEXT);")
    paid = {"title": "Blue-Eyes White Dragon LOB 1st Edition", "url": "https://www.ebay.com/itm/123456789012",
            "kinds": [], "printing_id": "p:LOB-EN001:UR:1st", "ask_home": 500.0,
            "scenarios": [{"printing_id": "p:LOB-EN001:UR:1st", "basis": "sales", "label": "1st Edition",
                           "weight": 1, "resale_mid": 800.0, "resale_low": 700.0, "profit_mid": 159.0,
                           "n_sales": 3, "all_in_home": 500.0}]}
    blind = {"title": "Dark Magician LOB no comps", "url": "https://www.ebay.com/itm/999999999999",
             "kinds": [], "printing_id": "p:LOB-EN005:UR:1st", "ask_home": 300.0, "scenarios": []}
    with st.db:
        st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (KEY, "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp", json.dumps(paid), "t", "t"))
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (KEY, "ebay_browse:EBAY_US", "123456789012", "INTL", paid["title"], paid["url"], None,
                       "ask", "500", "USD", None, "t", "t", None, "fp", "{}"))
        st.db.execute("INSERT INTO opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:999999999999", "SPECTATE", "OPEN", 1, None, 0, "[]", None, None, "fp",
                       json.dumps(blind), "t", "t"))
        st.db.execute("INSERT INTO listing VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("ebay_browse:EBAY_US:999999999999", "ebay_browse:EBAY_US", "999999999999", "INTL",
                       blind["title"], blind["url"], None, "ask", "300", "USD", None, "t", "t", None, "fp", "{}"))
    return st


def test_resolve_takes_a_link_an_item_number_or_a_key(tmp_path):
    st = seed(tmp_path)
    assert resolve(st, "https://www.ebay.com/itm/Blue-Eyes-White-Dragon/123456789012?hash=x") == KEY
    assert resolve(st, "123456789012") == KEY
    assert resolve(st, KEY) == KEY
    assert resolve(st, "123") is None and resolve(st, "") is None


def test_add_is_idempotent_and_remove_clears(tmp_path):
    st = seed(tmp_path)
    assert add(st, KEY, NOW) is True
    assert add(st, KEY, NOW) is False
    assert keys(st) == [KEY]
    remove(st, KEY)
    assert keys(st) == []


def test_view_prices_from_sales_and_flags_the_ones_without_a_comp(tmp_path):
    st = seed(tmp_path)
    add(st, KEY, NOW)
    add(st, "ebay_browse:EBAY_US:999999999999", NOW)
    page = view(st, None, M(), NOW, "tok")
    assert "Card Pile" in page and "Blue-Eyes White Dragon LOB 1st Edition" in page
    assert "1 of 2 price from real sales" in page
    assert "$159" in page and "0.32×" in page                      # 159 / 500
    assert "Best return per dollar" in page
    assert "Dark Magician LOB no comps" in page
    assert page.count('action="/pile/remove"') == 2
    assert page.count('class="pcard cut') == 2
    assert "The pile" in page and "Chases" in page


def test_view_with_an_empty_pile_says_so(tmp_path):
    page = view(seed(tmp_path), None, M(), NOW, "tok")
    assert "Nothing in the pile yet." in page
    assert 'data-zone="pile"' in page and 'data-zone="set"' in page and 'data-zone="chase"' in page
    assert "The Card Pile" in page and 'class="pad"' in page


def test_new_additions_land_in_the_pile_and_sort_left(tmp_path):
    st = seed(tmp_path)
    add(st, KEY, NOW)
    page = view(st, None, M(), NOW, "tok")
    assert 'data-side="pile"' in page
    assert 'class="zcol zc-pile"' in page and 'action="/pile/sort"' in page
    set_side(st, KEY, "set")
    page = view(st, None, M(), NOW, "tok")
    assert 'data-side="set"' in page and "Set aside" in page


def test_a_card_version_adds_and_renders_as_a_cutout(tmp_path):
    from cardman.pile import known
    st = seed(tmp_path)
    key = "card:pk:EN:sv1-249:holo@pkm-sv1-249"
    assert known(st, key) is True
    assert known(st, "card:no-at-sign") is False
    add(st, key, NOW)
    page = view(st, None, M(), NOW, "tok")
    assert 'data-key="card:pk:EN:sv1-249:holo@pkm-sv1-249"' in page
    assert "pkm-sv1-249" in page and "today" in page
    assert page.count('class="pcard cut') == 1


def test_a_tcgplayer_product_link_resolves_to_the_stored_ask(tmp_path):
    st = seed(tmp_path)
    with st.db:
        st.db.execute("INSERT INTO public_listing VALUES ('tcgplayer','1','42347','tcg:42347|Holofoil','Dragonite',"
                      "'4','Holofoil','Near Mint','English','10','1','ask',0,NULL,NULL,NULL,'ask','t')")
        st.db.execute("INSERT INTO public_listing VALUES ('tcgplayer','2','42347','tcg:42347|Holofoil','Dragonite',"
                      "'4','Holofoil','Damaged','English','1','0','ask',0,NULL,NULL,NULL,'ask','t')")
    assert resolve(st, "https://www.tcgplayer.com/product/42347/pokemon-fossil-dragonite") == "tcg:42347|Holofoil"
    add(st, "tcg:42347|Holofoil", NOW)
    page = view(st, None, M(), NOW, "tok")
    assert "tcg:42347|Holofoil" in page and "Dragonite" in page and "$11" in page


def test_sort_right_starts_a_chase_at_the_sold_typical(tmp_path):
    st = seed(tmp_path)
    st.db.executescript(
        "CREATE TABLE card_listing (passcode TEXT, key TEXT, card_prob REAL, printing_id TEXT, "
        "printing_prob REAL, lot INT, status TEXT);")
    with st.db:
        st.db.execute("INSERT INTO card_listing VALUES ('89631139', ?, 0.97, 'p:LOB-EN001:UR:1st', 0.94, 0, 'SEEN')",
                      (KEY,))
    add(st, KEY, NOW)
    assert sort(st, KEY, "chase", NOW, "USD") == "ok"
    c = chases(st)[0]
    assert c["passcode"] == "89631139" and c["threshold"] == 800.0     # sold typical, not the ask
    assert keys(st) == []
    assert "Chase under" in view(st, None, M(), NOW, "tok")


def test_sort_right_without_a_card_stays_in_the_pile(tmp_path):
    st = seed(tmp_path)
    add(st, KEY, NOW)
    assert sort(st, KEY, "chase", NOW, "USD") == "nocard"
    assert keys(st) == [KEY]
    assert sort(st, KEY, "sideways", NOW, "USD") == "bad"


def test_old_pile_rows_keep_their_place_after_the_side_column_lands(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    with st.db:
        st.db.execute("CREATE TABLE card_pile (key TEXT PRIMARY KEY, added_at TEXT NOT NULL)")
        st.db.execute("INSERT INTO card_pile VALUES ('old', 't')")
    assert add(st, "new", NOW) is True
    side = dict(st.db.execute("SELECT key, side FROM card_pile"))
    assert side == {"old": "set", "new": "pile"}
