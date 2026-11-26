"""The YGOPRODeck catalog bootstrap: normalize, index, resumable art download, version-diff refresh."""
import json

import pytest

from cardman import ygo_catalog as yc


def _card(pid, name, code="LOB-EN001"):
    return {"id": pid, "name": name, "type": "Normal Monster", "frameType": "normal", "race": "Dragon",
            "attribute": "LIGHT", "archetype": name.split()[0], "atk": 3000, "def": 2500, "level": 8,
            "desc": "text", "card_sets": [{"set_code": code, "set_name": "Legend of Blue Eyes",
                                           "set_rarity": "Ultra Rare", "set_rarity_code": "UR"}],
            "card_images": [{"image_url": f"https://images.ygoprodeck.com/images/cards/{pid}.jpg",
                             "image_url_small": f"https://images.ygoprodeck.com/images/cards_small/{pid}.jpg",
                             "image_url_cropped": f"https://images.ygoprodeck.com/images/cards_cropped/{pid}.jpg"}],
            "card_prices": [{"tcgplayer_price": "10.00"}]}


CARDS = [_card(89631139, "Blue-Eyes White Dragon"), _card(46986414, "Dark Magician", "SDY-001")]


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    d = tmp_path / "ygoprodeck"
    d.mkdir()
    monkeypatch.setattr(yc, "YGO_DIR", d)
    monkeypatch.setattr(yc, "RAW", d / "cardinfo.json")
    monkeypatch.setattr(yc, "INDEX", d / "catalog.sqlite")
    monkeypatch.setattr(yc, "PROGRESS", d / "images_progress.json")
    monkeypatch.setattr(yc, "IMAGES_FULL", tmp_path / "images" / "artwork" / "full")
    return d


def test_normalize_pulls_id_sets_and_images():
    r = yc.normalize_card(CARDS[0])
    assert r["id"] == "89631139" and r["name"] == "Blue-Eyes White Dragon"
    assert r["sets"][0]["set_code"] == "LOB-EN001" and r["sets"][0]["rarity"] == "Ultra Rare"
    assert r["image"].endswith("/cards/89631139.jpg") and r["image_small"].endswith("cards_small/89631139.jpg")
    assert r["atk"] == 3000 and r["level"] == 8


def test_build_index_writes_cards_printings_and_version(paths):
    n = yc.build_index(CARDS, "148.08")
    assert n == 2
    assert yc.known_ids() == {"89631139", "46986414"}
    import sqlite3
    db = sqlite3.connect(str(yc.INDEX))
    assert db.execute("SELECT COUNT(*) FROM printing").fetchone()[0] == 2
    assert db.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()[0] == "148.08"
    assert db.execute("SELECT set_code FROM printing WHERE id='46986414'").fetchone()[0] == "SDY-001"
    db.close()


def test_download_lands_full_art_in_the_imagecache_path_and_is_resumable(paths, monkeypatch):
    monkeypatch.setattr(yc, "_get", lambda url, timeout=30: b"\xff\xd8" + b"x" * 200)
    out = yc.download_images(CARDS, workers=2, rate=0)
    assert out["hit"] == 2 and out["fail"] == 0
    assert (yc.IMAGES_FULL / "89631139.jpg").exists()
    assert (yc.IMAGES_FULL / "46986414.jpg").exists()
    again = yc.download_images(CARDS, workers=2, rate=0)
    assert again["hit"] == 2  # existing files count as hits, never re-fetched


def test_download_marks_a_failure_and_skips_it_within_the_retry_window(paths, monkeypatch):
    def boom(url, timeout=30):
        raise OSError("host blocked")
    monkeypatch.setattr(yc, "_get", boom)
    out = yc.download_images(CARDS, workers=2, rate=0)
    assert out["fail"] == 2
    assert (yc.IMAGES_FULL / "89631139.jpg.miss").exists()
    again = yc.download_images(CARDS, workers=2, rate=0)
    assert again["skip"] == 2 and again["fail"] == 0


def test_refresh_only_flags_new_cards_when_the_version_moves(paths, monkeypatch):
    yc.build_index(CARDS, "1.0")
    monkeypatch.setattr(yc, "db_version", lambda: ("2.0", "2026-11-25 00:00:00"))
    newer = CARDS + [_card(12345678, "Brand New Monster", "NEW-EN001")]
    monkeypatch.setattr(yc, "fetch_catalog", lambda save=True: newer)
    out = yc.refresh(download=False)
    assert out["changed"] is True and out["new"] == 1 and "12345678" in out["new_ids"]
    assert yc.known_ids() == {"89631139", "46986414", "12345678"}


def test_refresh_is_a_noop_when_the_version_holds(paths, monkeypatch):
    yc.build_index(CARDS, "1.0")
    monkeypatch.setattr(yc, "db_version", lambda: ("1.0", "2026-11-23 00:00:00"))
    called = []
    monkeypatch.setattr(yc, "fetch_catalog", lambda save=True: called.append(1) or CARDS)
    out = yc.refresh(download=False)
    assert out["changed"] is False and not called


def test_status_reports_counts(paths):
    yc.build_index(CARDS, "148.08")
    s = yc.status()
    assert s["cards"] == 2 and s["printings"] == 2 and s["meta"]["db_version"] == "148.08"