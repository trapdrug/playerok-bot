from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.config import AppConfig
from app.db import Database
from app.playerok.client import PlayerokClient
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.automation import AutomationService
from app.services.cookies import CookieService
from app.services.notifications import NotificationService
from app.services.scheduler import SchedulerService
from app.services.settings import SettingsService


@dataclass(slots=True)
class AppContext:
    """Everything the bot handlers need, assembled once at startup."""

    config: AppConfig
    db: Database
    audit: AuditService
    auth: AuthService
    cookies: CookieService
    settings: SettingsService
    notifications: NotificationService
    client: PlayerokClient
    automation: AutomationService
    scheduler: SchedulerService
    background_tasks: set[asyncio.Task] = field(default_factory=set)


__all__ = ["AppContext"]
