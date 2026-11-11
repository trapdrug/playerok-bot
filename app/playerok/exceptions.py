from __future__ import annotations


class PlayerokError(Exception):
    """Base class for safe, user-reportable Playerok errors."""


class SessionExpiredError(PlayerokError):
    pass


class CaptchaError(PlayerokError):
    pass


class RateLimitError(PlayerokError):
    pass


class LayoutChangedError(PlayerokError):
    pass


class StopRequestedError(PlayerokError):
    pass


class FundingSourceError(PlayerokError):
    pass


class DailyLimitError(PlayerokError):
    pass


class PaymentUncertainError(PlayerokError):
    pass


__all__ = [
    "CaptchaError",
    "DailyLimitError",
    "FundingSourceError",
    "LayoutChangedError",
    "PaymentUncertainError",
    "PlayerokError",
    "RateLimitError",
    "SessionExpiredError",
    "StopRequestedError",
]
