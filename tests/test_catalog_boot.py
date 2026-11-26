"""A missing catalog is named, and a version with no TCGplayer row has no guide price."""
from cardman.catalog_boot import missing, version_market


def test_empty_workspace_is_missing_all_three_and_a_version_has_no_borrowed_price(tmp_path):
    assert missing(tmp_path) == ["pkm", "ygo"]
    assert version_market("pk:EN:sv3pt5-199:holo@pkm-sv3pt5-199", tmp_path / "data" / "cache" / "catalog.sqlite") is None
