"""Tests never read the real .env or use real keys."""
import pytest


@pytest.fixture(autouse=True)
def no_real_settings(monkeypatch):
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_DELETIONS_URL", "EBAY_DELETIONS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("cardman.settings.load_env", lambda path=None: [])
