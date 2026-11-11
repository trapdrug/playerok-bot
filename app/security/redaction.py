from __future__ import annotations

import logging
import re
from typing import Iterable


class SecretRedactionFilter(logging.Filter):
    """Defence in depth: keep known secrets out of every log record."""

    _TELEGRAM_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
    _COOKIE_PAIR_RE = re.compile(
        r"(?i)((?:cookie|set-cookie|cookies)\s*[:=]\s*)([^\s;]{8,})"
    )

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        redacted = self._TELEGRAM_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)
        redacted = self._COOKIE_PAIR_RE.sub(r"\1[REDACTED]", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


__all__ = ["SecretRedactionFilter"]
