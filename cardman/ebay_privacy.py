"""eBay user data in CardMan: keep as little as possible, and erase it on request.

eBay's Marketplace Account Deletion rule: when an eBay user closes their account, any personal data an app
stored about them must be deleted. CardMan only ever sees sellers (from public listings), and keeps:

* No seller username or user id in readable form. `seller_token()` replaces a username with a keyed hash
  (HMAC-SHA256 with a secret that never leaves this computer), so "same seller listed 12 items this week" still
  works, while the stored value can't be turned back into a name.
* Raw eBay pages are stored with the seller block removed (`strip_user_fields`).
* Erasing one user (on eBay's deletion notice, or by hand) is `cardman.ebay_deletion`, which also erases
  these hashes:  python -m cardman.ebay_deletion purge --username NAME
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SALT_FILE = ROOT / "data" / "privacy_salt"
USER_KEYS = ("seller", "username", "userId", "feedbackScore", "feedbackPercentage", "sellerAccountType",
             "sellerLegalInfo", "contactInfo")


def _salt() -> bytes:
    env = os.environ.get("CARDMAN_PRIVACY_SALT")
    if env:
        return env.encode()
    if not SALT_FILE.exists():
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            SALT_FILE.write_text(secrets.token_hex(32))
        finally:
            os.umask(old)
    return SALT_FILE.read_text().strip().encode()


def seller_token(username: str | None) -> str | None:
    if not username:
        return None
    if username.startswith("seller:"):
        return username
    return "seller:" + hmac.new(_salt(), username.strip().lower().encode(), hashlib.sha256).hexdigest()[:20]


def strip_user_fields(obj):
    """A copy of an eBay API payload with seller identity removed (prices, titles and item ids stay)."""
    if isinstance(obj, dict):
        return {k: strip_user_fields(v) for k, v in obj.items() if k not in USER_KEYS}
    if isinstance(obj, list):
        return [strip_user_fields(v) for v in obj]
    return obj
