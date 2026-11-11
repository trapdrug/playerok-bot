from __future__ import annotations

import bcrypt


class PasswordValidationError(ValueError):
    pass


# Hash used only to equalise timing for unknown usernames.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


def validate_password(password: str, *, username: str | None = None) -> None:
    if not password:
        raise PasswordValidationError("Пароль не может быть пустым")
    if len(password) < 8:
        raise PasswordValidationError("Пароль должен содержать минимум 8 символов")
    if len(password.encode("utf-8")) > 72:
        raise PasswordValidationError("Пароль слишком длинный (максимум 72 байта)")
    if username and password.lower() == username.lower():
        raise PasswordValidationError("Пароль не должен совпадать с логином")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = (password_hash or DUMMY_PASSWORD_HASH.decode("ascii")).encode("ascii")
    try:
        return bcrypt.checkpw(password.encode("utf-8"), candidate)
    except (ValueError, TypeError):
        return False


__all__ = [
    "DUMMY_PASSWORD_HASH",
    "PasswordValidationError",
    "hash_password",
    "validate_password",
    "verify_password",
]
