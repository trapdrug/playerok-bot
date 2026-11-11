from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.config import AppConfig
from app.db import Database
from app.models import PlayerokSession, User
from app.security.crypto import CookieCipher, CookieDecryptionError
from app.services.audit import AuditService
from app.utils.parsing import utcnow


logger = logging.getLogger(__name__)


class CookieFormatError(ValueError):
    pass


_ALLOWED_DOMAINS = ("playerok.com",)


def _domain_allowed(domain: str) -> bool:
    normalised = domain.lstrip(".").lower()
    return any(
        normalised == allowed or normalised.endswith("." + allowed)
        for allowed in _ALLOWED_DOMAINS
    )


def _normalise_same_site(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    mapping = {
        "strict": "Strict",
        "lax": "Lax",
        "none": "None",
        "no_restriction": "None",
        "unspecified": "Lax",
        "": None,
    }
    return mapping.get(text, "Lax")


def _extract_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("cookies", "Cookies", "data"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        # Simple {"name": "value"} export.
        if raw and all(isinstance(value, (str, int, float, bool)) for value in raw.values()):
            return [{"name": key, "value": value} for key, value in raw.items()]
    raise CookieFormatError(
        "Ожидается JSON-массив cookies или объект с полем cookies"
    )


def normalise_cookies(
    raw: Any,
    *,
    default_domain: str = ".playerok.com",
    max_count: int = 500,
) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in _extract_list(raw):
        name = item.get("name", item.get("Name"))
        value = item.get("value", item.get("Value"))
        if name is None:
            continue
        name = str(name).strip()
        if not name or len(name) > 256:
            continue
        if value is None:
            value = ""
        value = str(value)

        domain = str(
            item.get("domain") or item.get("Domain") or default_domain
        ).strip()
        if not _domain_allowed(domain):
            continue

        path = str(item.get("path") or item.get("Path") or "/")
        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)

        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": bool(item.get("secure", item.get("Secure", True))),
            "httpOnly": bool(item.get("httpOnly", item.get("HttpOnly", False))),
        }

        expires = item.get("expires", item.get("expirationDate", item.get("Expires")))
        if expires not in (None, "", "session", "Session"):
            try:
                expires_float = float(expires)
                if expires_float > 0:
                    cookie["expires"] = expires_float
            except (TypeError, ValueError):
                pass

        same_site = _normalise_same_site(
            item.get("sameSite", item.get("SameSite"))
        )
        if same_site:
            cookie["sameSite"] = same_site

        cookies.append(cookie)
        if len(cookies) >= max_count:
            break

    if not cookies:
        raise CookieFormatError("В JSON не найдено ни одной cookie для playerok.com")
    return cookies


@dataclass(slots=True)
class CookieStatus:
    exists: bool
    updated_at: Any = None
    is_alive: bool | None = None
    last_checked_at: Any = None
    last_error: str | None = None


class CookieService:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        cipher: CookieCipher,
        audit: AuditService,
    ) -> None:
        self.db = db
        self.config = config
        self.cipher = cipher
        self.audit = audit

    async def save_from_bytes(self, payload: bytes, actor: User) -> int:
        if not payload:
            raise CookieFormatError("Файл пустой")
        if len(payload) > self.config.max_cookie_file_bytes:
            raise CookieFormatError("Файл cookies слишком большой")
        try:
            raw = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CookieFormatError("Файл не является корректным JSON") from exc

        cookies = normalise_cookies(
            raw, default_domain=".playerok.com", max_count=500
        )
        encrypted = self.cipher.encrypt(cookies)

        async with self.db.session() as session:
            row = await session.get(PlayerokSession, 1)
            if row is None:
                row = PlayerokSession(id=1, cookies_encrypted=encrypted)
                session.add(row)
            else:
                row.cookies_encrypted = encrypted
            row.cookies_updated_at = utcnow()
            row.cookies_updated_by = actor.id
            row.is_alive = None
            row.last_error = None
            await self.audit.log(
                "cookies_updated",
                f"Загружено cookies: {len(cookies)}",
                user=actor,
                session=session,
            )
        return len(cookies)

    async def get_cookies(self) -> list[dict[str, Any]] | None:
        async with self.db.session() as session:
            row = await session.get(PlayerokSession, 1)
            if row is None or not row.cookies_encrypted:
                return None
            try:
                return self.cipher.decrypt(row.cookies_encrypted)
            except CookieDecryptionError:
                logger.error("Stored cookies cannot be decrypted; FERNET_KEY changed?")
                return None

    async def status(self) -> CookieStatus:
        async with self.db.session() as session:
            row = await session.get(PlayerokSession, 1)
            if row is None:
                return CookieStatus(exists=False)
            return CookieStatus(
                exists=bool(row.cookies_encrypted),
                updated_at=row.cookies_updated_at,
                is_alive=row.is_alive,
                last_checked_at=row.last_checked_at,
                last_error=row.last_error,
            )

    async def mark_checked(self, alive: bool, error: str | None = None) -> None:
        async with self.db.session() as session:
            row = await session.get(PlayerokSession, 1)
            if row is None:
                return
            row.is_alive = alive
            row.last_checked_at = utcnow()
            row.last_error = (error or "")[:300] or None

    async def clear(self, actor: User) -> None:
        async with self.db.session() as session:
            row = await session.get(PlayerokSession, 1)
            if row is not None:
                row.cookies_encrypted = ""
                row.is_alive = False
                row.last_checked_at = utcnow()
                row.last_error = "cookies removed"
            await self.audit.log(
                "cookies_removed", "Cookies удалены", user=actor, session=session
            )


__all__ = [
    "CookieFormatError",
    "CookieService",
    "CookieStatus",
    "normalise_cookies",
]
