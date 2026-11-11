from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ChangePassword(StatesGroup):
    waiting_old = State()
    waiting_new = State()


__all__ = ["ChangePassword"]
