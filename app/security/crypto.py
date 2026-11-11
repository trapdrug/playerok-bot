from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CookieDecryptionError(Exception):
    """Raised when encrypted cookie data cannot be decrypted."""


class CookieCipher:
    """Fernet wrapper for cookie payloads. Never logs plaintext."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:  # noqa: BLE001 - re-raised as configuration error
            raise ValueError("FERNET_KEY is invalid") from exc

    def encrypt(self, payload: list[dict[str, Any]]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, token: str) -> list[dict[str, Any]]:
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CookieDecryptionError("Не удалось расшифровать cookies") from exc
        if not isinstance(data, list):
            raise CookieDecryptionError("Некорректный формат cookies")
        return data


__all__ = ["CookieCipher", "CookieDecryptionError"]
