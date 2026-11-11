from __future__ import annotations

import pytest

from app.security.passwords import (
    PasswordValidationError,
    hash_password,
    validate_password,
    verify_password,
)


def test_hash_and_verify():
    hashed = hash_password("strong-pass")
    assert hashed != "strong-pass"
    assert verify_password("strong-pass", hashed)
    assert not verify_password("wrong-pass", hashed)


def test_verify_unknown_hash():
    assert not verify_password("anything", None)


def test_validate_password():
    validate_password("12345678")
    with pytest.raises(PasswordValidationError):
        validate_password("short")
    with pytest.raises(PasswordValidationError):
        validate_password("sameuser", username="sameuser")
    with pytest.raises(PasswordValidationError):
        validate_password("x" * 80)
