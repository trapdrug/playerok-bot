from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from zoneinfo import ZoneInfo


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
MONEY_RE = re.compile(r"(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)")


def utcnow() -> datetime:
    """Naive UTC timestamp used consistently in SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def local_date(tz_name: str) -> date:
    return local_now(tz_name).date()


def to_local(value: datetime | None, tz_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(tz_name))


def format_dt(value: datetime | None, tz_name: str) -> str:
    local = to_local(value, tz_name)
    return local.strftime("%d.%m.%Y %H:%M:%S") if local else "—"


def normalise_username(value: str) -> str:
    return value.strip().lower()


def validate_username(value: str) -> str:
    username = normalise_username(value)
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Логин: 3-32 символа, только латиница, цифры и _")
    return username


def parse_money(value: str | int | float | Decimal | None) -> Decimal | None:
    """Parse Russian-formatted money into a Decimal rounded to kopecks."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    text = str(value).replace("\u00a0", " ").replace("\u202f", " ")
    match = MONEY_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(",", ".")
    try:
        return Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def money(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount == amount.to_integral_value():
        amount = amount.quantize(Decimal("1"))
    return f"{amount:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def truncate(value: str, limit: int = 500) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = [
    "USERNAME_RE",
    "format_dt",
    "local_date",
    "local_now",
    "money",
    "normalise_username",
    "parse_money",
    "to_local",
    "truncate",
    "utcnow",
    "validate_username",
]
