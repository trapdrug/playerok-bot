from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """Application configuration. Secrets are loaded only from environment/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr
    admin_login: str = "gladmin"
    admin_initial_password: SecretStr

    # Storage / crypto
    database_url: str = "sqlite+aiosqlite:///./data/playerok.db"
    fernet_key: SecretStr
    data_dir: Path = Path("data")

    # Playerok
    playerok_base_url: str = "https://playerok.com"
    playerok_username: str | None = None
    selectors_file: Path = Path("selectors.yaml")
    headless: bool = True
    browser_timeout_ms: int = 45_000
    navigation_timeout_ms: int = 60_000
    # Playerok shows a "browser verification" page for unknown IP addresses.
    # The page verifies itself and reloads; the bot waits and retries.
    challenge_wait_seconds: int = 15
    challenge_max_attempts: int = 4
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Automation
    dry_run: bool = True
    poll_interval_seconds: int = 60
    action_delay_min_seconds: float = 3.0
    action_delay_max_seconds: float = 12.0
    max_sales_per_cycle: int = 5
    max_cookie_file_bytes: int = 1_000_000

    # Misc
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"

    @field_validator("admin_login")
    @classmethod
    def _normalise_admin_login(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("ADMIN_LOGIN must not be empty")
        return value

    @field_validator("playerok_base_url")
    @classmethod
    def _normalise_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("PLAYEROK_BASE_URL must start with http:// or https://")
        return value

    @field_validator("poll_interval_seconds")
    @classmethod
    def _validate_interval(cls, value: int) -> int:
        if value < 10:
            raise ValueError("POLL_INTERVAL_SECONDS must be >= 10")
        return value

    @field_validator("action_delay_max_seconds")
    @classmethod
    def _validate_delay(cls, value: float, info) -> float:
        minimum = info.data.get("action_delay_min_seconds", 3.0)
        if value < minimum:
            raise ValueError("ACTION_DELAY_MAX_SECONDS must be >= ACTION_DELAY_MIN_SECONDS")
        return value

    @model_validator(mode="after")
    def _validate_secrets(self) -> "AppConfig":
        token = self.bot_token.get_secret_value().strip()
        if ":" not in token:
            raise ValueError("BOT_TOKEN does not look like a Telegram bot token")
        fernet = self.fernet_key.get_secret_value().strip()
        if len(fernet) < 40:
            raise ValueError("FERNET_KEY is invalid; generate one with Fernet.generate_key()")
        initial = self.admin_initial_password.get_secret_value()
        if not initial:
            raise ValueError("ADMIN_INITIAL_PASSWORD must not be empty")
        return self

    @property
    def database_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url[len(prefix) :])
        return None

    @property
    def selectors_path(self) -> Path:
        return self.selectors_file


__all__ = ["AppConfig"]
