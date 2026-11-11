from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import AppConfig
from app.services.automation import AutomationService
from app.utils.parsing import local_now


logger = logging.getLogger(__name__)


class SchedulerService:
    """Thin APScheduler wrapper around :class:`AutomationService`."""

    def __init__(
        self, automation: AutomationService, config: AppConfig
    ) -> None:
        self.automation = automation
        self.config = config
        self._scheduler: AsyncIOScheduler | None = None

    def start(self) -> None:
        if self._scheduler is not None:
            return

        scheduler = AsyncIOScheduler(timezone=self.config.timezone)
        scheduler.add_job(
            self._tick,
            trigger="interval",
            seconds=self.config.poll_interval_seconds,
            id="playerok_poll",
            name="Playerok poll",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=max(30, self.config.poll_interval_seconds),
            next_run_time=local_now(self.config.timezone)
            + timedelta(seconds=5),
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.info(
            "Scheduler started: poll every %s seconds",
            self.config.poll_interval_seconds,
        )

    async def _tick(self) -> None:
        try:
            await self.automation.run_cycle()
        except Exception:  # noqa: BLE001 - scheduler must keep running
            logger.exception("Scheduled automation tick failed")

    def shutdown(self) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler shutdown failed")
        finally:
            self._scheduler = None

    @property
    def next_run_time(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job("playerok_poll")
        return getattr(job, "next_run_time", None) if job else None


__all__ = ["SchedulerService"]
