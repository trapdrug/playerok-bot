from __future__ import annotations

import pytest

from app.services.cookies import CookieFormatError, normalise_cookies


def test_list_export_deduplicates_and_filters_domains():
    cookies = normalise_cookies(
        [
            {"name": "a", "value": "1", "domain": ".playerok.com", "path": "/"},
            {"name": "a", "value": "2", "domain": ".playerok.com", "path": "/"},
            {"name": "evil", "value": "x", "domain": ".example.com"},
        ]
    )
    assert len(cookies) == 1
    assert cookies[0]["name"] == "a"
    assert cookies[0]["value"] == "1"


def test_wrapper_export():
    cookies = normalise_cookies(
        {"cookies": [{"name": "session", "value": "abc"}]}
    )
    assert cookies[0]["domain"] == ".playerok.com"


def test_simple_object_export():
    cookies = normalise_cookies({"session": "abc", "token": "def"})
    assert {cookie["name"] for cookie in cookies} == {"session", "token"}


def test_invalid_exports():
    with pytest.raises(CookieFormatError):
        normalise_cookies({"cookies": []})
    with pytest.raises(CookieFormatError):
        normalise_cookies(
            [{"name": "x", "value": "1", "domain": ".example.com"}]
        )


def test_expires_and_same_site():
    cookies = normalise_cookies(
        [
            {
                "name": "x",
                "value": "1",
                "domain": "playerok.com",
                "expires": "1700000000",
                "sameSite": "no_restriction",
            }
        ]
    )
    assert cookies[0]["expires"] == 1700000000.0
    assert cookies[0]["sameSite"] == "None"
