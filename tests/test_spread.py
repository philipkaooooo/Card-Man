"""The keyword scanner must not run. A search is not a printing."""
import pytest

from cardman import market_live, spread
from cardman.market_live import Removed


def test_keyword_sold_scrape_refuses():
    with pytest.raises(Removed):
        market_live.ebay_sold("LOB-001")
    with pytest.raises(Removed):
        market_live.yahoo_jp_closed("青眼の白龍")
    with pytest.raises(Removed):
        spread.scan_spreads(["LOB-001"])
