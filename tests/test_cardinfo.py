"""Card search, facts, printing scans and images, on real YAML Yugi records (pinned subset). No network."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cardman.cardinfo import PLACEHOLDER, CardIndex, ImageCache, _card_record
from cardman.ui import make_handler
from tests.real_catalog import DATA, real_catalog


@pytest.fixture(scope="module")
def idx(tmp_path_factory):
    return CardIndex(cards_dir=DATA, db_path=tmp_path_factory.mktemp("ci") / "cardinfo.sqlite", catalog=real_catalog())


def test_every_result_is_one_passcode_and_exact_queries_are_exact(idx):
    assert idx.search("89631139")[0].passcode == "89631139"
    assert idx.search("LOB-EN001")[0].passcode == "89631139"
    assert idx.search("Blue-Eyes White Dragon")[0].passcode == "89631139"
    assert idx.search("青眼の白龍")[0].passcode == "89631139"
    assert idx.search("ブルーアイズ・ホワイト・ドラゴン")[0].passcode == "89631139"
    assert all(h.passcode.isdigit() for h in idx.search("dragon"))


def test_typos_and_old_printed_names_find_the_card(idx):
    assert idx.search("Blue-Eyes Whte Dragon")[0].passcode == "89631139"
    assert idx.search("dark magican")[0].name == "Dark Magician"


def test_dark_magician_girl_is_not_dark_magician(idx):
    top = idx.search("Dark Magician")[0]
    assert top.name == "Dark Magician" and top.score >= 90


def test_alternate_artworks_stay_separate_cards(idx):
    dm = [h for h in idx.search("Dark Magician", 10) if h.name == "Dark Magician"]
    assert len({h.passcode for h in dm}) == len(dm) >= 2
    assert any("artwork" in s["matched"] for s in idx.suggest("Dark Magician") if s["name"] == "Dark Magician")


def test_card_facts_are_complete_and_ruby_free(idx):
    c = idx.card("89631139")
    assert (c["name_en"], c["name_ja"], c["reading"]) == ("Blue-Eyes White Dragon", "青眼の白龍", "ブルーアイズ・ホワイト・ドラゴン")
    full = _card_record({"password": 5, "konami_id": 9, "name": {"en": "A", "ja": "<ruby>甲<rt>こう</rt></ruby>"},
                         "text": {"en": "line one\nline two", "ja": "<ruby>効<rt>こう</rt></ruby>果"}, "card_type": "Monster",
                         "monster_type_line": "Dragon / Effect", "attribute": "DARK", "level": 4, "atk": 1800, "def": 0,
                         "pendulum_effect": {"en": "p"}, "pendulum_scale": 3, "link_arrows": ["↙"], "limit_regulation": {"tcg": "Limited"}})
    assert (full["name_ja"], full["reading"], full["text_ja"]) == ("甲", "こう", "効果")
    assert (full["atk"], full["def"], full["level"], full["scale"], full["pend_en"]) == (1800, 0, 4, 3, "p")


def test_real_checkout_has_full_stats_when_present():
    from cardman.cardinfo import CARDS_DIR
    if not (CARDS_DIR / "89631139.json").exists():
        pytest.skip("YAML Yugi checkout not synced on this machine")
    c = _card_record(json.loads((CARDS_DIR / "89631139.json").read_text(encoding="utf-8")))
    assert (c["atk"], c["def"], c["level"], c["attribute"]) == (3000, 2500, 8, "LIGHT") and c["text_en"]


def test_printings_come_from_the_catalog_for_that_passcode_only(idx):
    pr = idx.printings("89631139")
    assert pr and all(p.artwork_id == "art:pw89631139" for p in pr)
    assert {p.region.value for p in pr} <= {"EN_TCG", "JP_OCG"}


def test_a_scan_is_only_matched_to_exactly_its_printing(idx):
    rec = {"password": 1, "name": {"en": "X"}, "images": [{"image": "X-LOB-EN-UR-1E.png"}, {"image": "X-SDK-EN-C-UE.png"}]}
    i = CardIndex(cards_dir=DATA, db_path=":memory:")
    i._loaded, i.cards = True, {"1": _card_record(rec)}
    from cardman.models import Printing, Region
    P = lambda code, rar, ed=None, region=Region.EN_TCG: Printing("p", "f", "art:pw1", region, "en", "x", rar, set_code=code, edition=ed)
    assert i.printing_scan("1", P("LOB-EN001", "Ultra Rare", "1st")) == "X-LOB-EN-UR-1E.png"
    assert i.printing_scan("1", P("LOB-EN001", "Ultra Rare")) == "X-LOB-EN-UR-1E.png"         # edition unknown: same set/rarity
    assert i.printing_scan("1", P("LOB-EN001", "Ultra Rare", "Unlimited")) is None           # wrong edition: no image
    assert i.printing_scan("1", P("LOB-EN001", "Secret Rare")) is None                        # wrong rarity
    assert i.printing_scan("1", P("LOB-JP001", "Ultra Rare", region=Region.JP_OCG)) is None  # wrong region
    assert i.printing_scan("1", P("SDK-E001", "Common")) == "X-SDK-EN-C-UE.png"


def test_images_download_once_and_fail_safely(tmp_path):
    calls = []

    def get(url):
        calls.append(url)
        return b"\xff\xd8jpeg", "image/jpeg"
    c = ImageCache(tmp_path, get=get)
    assert c.artwork("89631139", "small") == (b"\xff\xd8jpeg", "image/jpeg")
    c.artwork("89631139", "small")
    assert len(calls) == 1 and "cards_small/89631139.jpg" in calls[0]
    assert c.artwork("../etc", "")[1] == "image/svg+xml" and c.artwork("1", "huge")[1] == "image/svg+xml"

    def down(url):
        calls.append(url)
        raise OSError("offline")
    d = ImageCache(tmp_path / "d", get=down)
    assert d.artwork("1")[0] == PLACEHOLDER and d.artwork("1")[0] == PLACEHOLDER
    assert sum("/1.jpg" in u for u in calls) == 1            # a miss is not retried on every page view
    html_body = ImageCache(tmp_path / "h", get=lambda u: (b"<html>", "text/html"))
    assert html_body.artwork("2")[0] == PLACEHOLDER          # a non-image answer is never cached as an image


@pytest.fixture
def server(tmp_path, idx):
    fetched = []
    images = ImageCache(tmp_path / "img", get=lambda u: (fetched.append(u), (b"\xff\xd8x", "image/jpeg"))[1])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(tmp_path / "s.sqlite"), "t", cards=idx, images=images))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path, cookie=None):
        req = urllib.request.Request(base + path, headers={"Cookie": cookie} if cookie else {})
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    yield get, fetched
    srv.shutdown()


def test_pages_render_and_link_by_passcode(server):
    get, _ = server
    s, h, body = get("/")
    assert s == 200 and b"Find the exact card." in body and b"img-src 'self' data:" in h["Content-Security-Policy"].encode()
    s, _, body = get("/card/89631139")
    assert b"Blue-Eyes White Dragon" in body and b"89631139" in body and b"versions of this card" in body and "青眼の白龍".encode() in body
    _, _, body = get("/search?q=dragon")
    assert b"/card/" in body
    _, _, body = get("/", cookie="theme=vault")
    assert b'data-theme="vault"' in body and b"The Vault" in body


def test_exact_search_goes_straight_to_the_card(server):
    get, _ = server
    s, _, body = get("/search?q=89631139")         # urllib follows the 303
    assert b"Blue-Eyes White Dragon" in body and b"Card number" in body and b"89631139" in body


def test_suggestions_and_images_are_served_locally(server):
    get, fetched = server
    _, h, body = get("/api/suggest?q=blue-eyes")
    assert json.loads(body)["results"][0]["passcode"] == "89631139" and "json" in h["Content-Type"]
    s, h, body = get("/img/card/89631139.jpg?size=small")
    assert body == b"\xff\xd8x" and h["Content-Type"] == "image/jpeg" and "max-age" in h["Cache-Control"]
    _, h, body = get("/img/scan/not-a-known-file.png")
    assert h["Content-Type"] == "image/svg+xml" and not any("yugipedia" in u for u in fetched)   # unknown files are never fetched
