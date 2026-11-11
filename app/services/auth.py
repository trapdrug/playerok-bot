from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import AppConfig
from app.db import Database
from app.models import User
from app.security.passwords import (
    PasswordValidationError,
    hash_password,
    validate_password,
    verify_password,
)
from app.services.audit import AuditService
from app.utils.parsing import normalise_username, utcnow, validate_username


LOCK_AFTER_ATTEMPTS = 5
LOCK_MINUTES = 15


class AuthError(Exception):
    def __init__(self, message: str, *, code: str = "auth_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(slots=True)
class LoginResult:
    user: User
    must_change_password: bool


class AuthService:
    def __init__(self, db: Database, config: AppConfig, audit: AuditService) -> None:
        self.db = db
        self.config = config
        self.audit = audit

    async def ensure_main_admin(self) -> User:
        async with self.db.session() as session:
            existing = await session.scalar(
                select(User).where(User.role == "main_admin").limit(1)
            )
            if existing is not None:
                return existing

            username = normalise_username(self.config.admin_login)
            taken = await session.scalar(select(User).where(User.username == username))
            if taken is not None:
                # A partially initialised database. Promote the configured account
                # instead of creating a duplicate username.
                taken.role = "main_admin"
                taken.is_active = True
                taken.must_change_password = True
                await self.audit.log(
                    "main_admin_promoted",
                    "Аккаунт назначен главным администратором",
                    user=taken,
                    session=session,
                )
                return taken

            admin = User(
                username=username,
                password_hash=hash_password(
                    self.config.admin_initial_password.get_secret_value()
                ),
                role="main_admin",
                is_active=True,
                must_change_password=True,
            )
            session.add(admin)
            await session.flush()
            await self.audit.log(
                "main_admin_created",
                "Создан главный администратор при первом запуске",
                user=admin,
                session=session,
            )
            return admin

    async def authenticate(
        self, username: str, password: str, telegram_id: int
    ) -> LoginResult:
        username = normalise_username(username)
        async with self.db.session() as session:
            user = await session.scalar(select(User).where(User.username == username))
            now = utcnow()

            if user is None:
                verify_password(password, None)
                await self.audit.log(
                    "login_failed",
                    f"Неизвестный логин: {username[:32]}",
                    session=session,
                )
                await session.commit()
                raise AuthError("Неверный логин или пароль", code="invalid_credentials")

            if user.locked_until and user.locked_until > now:
                remaining = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
                raise AuthError(
                    f"Аккаунт заблокирован на {remaining} мин. из-за неверных попыток",
                    code="locked",
                )

            if not user.is_active:
                raise AuthError("Аккаунт отключён", code="inactive")

            if not verify_password(password, user.password_hash):
                user.failed_attempts += 1
                if user.failed_attempts >= LOCK_AFTER_ATTEMPTS:
                    user.failed_attempts = 0
                    user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                    await self.audit.log(
                        "login_locked",
                        f"Блокировка на {LOCK_MINUTES} минут",
                        user=user,
                        session=session,
                    )
                    await session.commit()
                    raise AuthError(
                        f"Слишком много неверных попыток. Блокировка на {LOCK_MINUTES} мин.",
                        code="locked",
                    )
                await self.audit.log(
                    "login_failed",
                    f"Неверный пароль, попытка {user.failed_attempts}/{LOCK_AFTER_ATTEMPTS}",
                    user=user,
                    session=session,
                )
                await session.commit()
                raise AuthError("Неверный логин или пароль", code="invalid_credentials")

            # A successful login re-binds the Telegram ID to this account.
            other = await session.scalar(
                select(User).where(
                    User.telegram_id == telegram_id, User.id != user.id
                )
            )
            if other is not None:
                other.telegram_id = None
                await self.audit.log(
                    "telegram_unbound",
                    "Telegram отвязан после входа в другой аккаунт",
                    user=other,
                    session=session,
                )

            user.telegram_id = telegram_id
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            await self.audit.log(
                "login_success",
                "Вход выполнен",
                user=user,
                session=session,
            )
            return LoginResult(user=user, must_change_password=user.must_change_password)

    async def change_password(
        self,
        user: User,
        old_password: str,
        new_password: str,
        *,
        forced: bool = False,
    ) -> None:
        if not forced and not verify_password(old_password, user.password_hash):
            raise AuthError("Текущий пароль неверен", code="invalid_password")
        try:
            validate_password(new_password, username=user.username)
        except PasswordValidationError as exc:
            raise AuthError(str(exc), code="weak_password") from exc
        if verify_password(new_password, user.password_hash):
            raise AuthError("Новый пароль должен отличаться от старого", code="same_password")

        async with self.db.session() as session:
            db_user = await session.get(User, user.id)
            if db_user is None or not db_user.is_active:
                raise AuthError("Аккаунт недоступен", code="inactive")
            db_user.password_hash = hash_password(new_password)
            db_user.must_change_password = False
            db_user.failed_attempts = 0
            db_user.locked_until = None
            await self.audit.log(
                "password_changed",
                "Пароль изменён",
                user=db_user,
                session=session,
            )

    async def create_account(
        self,
        actor: User,
        username: str,
        password: str,
        role: str = "user",
    ) -> User:
        if not actor.is_main_admin:
            raise AuthError("Недостаточно прав", code="forbidden")
        role_aliases = {
            "user": "user",
            "пользователь": "user",
            "worker": "worker",
            "работник": "worker",
        }
        role = role_aliases.get(role.strip().lower(), "")
        if not role:
            raise AuthError(
                "Роль должна быть user или worker", code="validation"
            )
        try:
            username = validate_username(username)
            validate_password(password, username=username)
        except (ValueError, PasswordValidationError) as exc:
            raise AuthError(str(exc), code="validation") from exc

        async with self.db.session() as session:
            exists = await session.scalar(select(User).where(User.username == username))
            if exists is not None:
                raise AuthError("Логин уже занят", code="exists")
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
                notifications_enabled=True,
                must_change_password=True,
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise AuthError("Логин уже занят", code="exists") from exc
            role_label = "работник" if role == "worker" else "пользователь"
            await self.audit.log(
                "account_created",
                f"Создан аккаунт {username} ({role_label})",
                user=actor,
                session=session,
            )
            return user

    async def set_notifications(self, user: User, enabled: bool) -> User:
        """Toggle notifications only for this account, never globally."""
        async with self.db.session() as session:
            db_user = await session.get(User, user.id)
            if db_user is None or not db_user.is_active:
                raise AuthError("Аккаунт недоступен", code="inactive")
            db_user.notifications_enabled = bool(enabled)
            await self.audit.log(
                "notifications_changed",
                f"Уведомления: {'✅' if enabled else '❌'}",
                user=db_user,
                session=session,
            )
            return db_user

    async def list_users(self) -> list[User]:
        async with self.db.session() as session:
            result = await session.scalars(select(User).order_by(User.created_at.asc()))
            return list(result.all())

    async def get_by_id(self, user_id: int) -> User | None:
        async with self.db.session() as session:
            return await session.get(User, user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        async with self.db.session() as session:
            return await session.scalar(
                select(User).where(User.telegram_id == telegram_id)
            )

    async def get_by_username(self, username: str) -> User | None:
        async with self.db.session() as session:
            return await session.scalar(
                select(User).where(User.username == normalise_username(username))
            )

    async def set_active(self, actor: User, user_id: int, active: bool) -> User:
        if not actor.is_main_admin:
            raise AuthError("Недостаточно прав", code="forbidden")
        async with self.db.session() as session:
            target = await session.get(User, user_id)
            if target is None:
                raise AuthError("Аккаунт не найден", code="not_found")
            if target.role == "main_admin":
                raise AuthError("Главного администратора нельзя отключить", code="forbidden")
            target.is_active = active
            await self.audit.log(
                "account_enabled" if active else "account_disabled",
                f"Аккаунт {target.username}: {'включён' if active else 'отключён'}",
                user=actor,
                session=session,
            )
            return target

    async def delete_account(self, actor: User, user_id: int) -> None:
        if not actor.is_main_admin:
            raise AuthError("Недостаточно прав", code="forbidden")
        if actor.id == user_id:
            raise AuthError("Нельзя удалить собственный аккаунт", code="forbidden")
        async with self.db.session() as session:
            target = await session.get(User, user_id)
            if target is None:
                raise AuthError("Аккаунт не найден", code="not_found")
            if target.role == "main_admin":
                raise AuthError("Главного администратора нельзя удалить", code="forbidden")
            username = target.username
            await self.audit.log(
                "account_deleted",
                f"Удалён аккаунт {username}",
                user=actor,
                session=session,
            )
            await session.execute(delete(User).where(User.id == user_id))

    async def logout(self, user: User) -> None:
        async with self.db.session() as session:
            db_user = await session.get(User, user.id)
            if db_user is None:
                return
            db_user.telegram_id = None
            await self.audit.log(
                "logout", "Telegram отвязан", user=db_user, session=session
            )


__all__ = [
    "AuthError",
    "AuthService",
    "LOCK_AFTER_ATTEMPTS",
    "LOCK_MINUTES",
    "LoginResult",
]
