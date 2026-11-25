from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.helpers import require_automation_cb, safe_answer
from app.bot.keyboards import back_menu
from app.context import AppContext
from app.models import User
from app.playerok.exceptions import PlayerokError

router = Router(name="manual_boost")
logger = logging.getLogger(__name__)
PAGE_SIZE = 8


@dataclass
class Selection:
    seller: str
    items: list[tuple[str, str]]
    dry_run: bool
    token: str = field(default_factory=lambda: secrets.token_hex(6))
    selected: set[int] = field(default_factory=set)
    page: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def keyboard(self) -> InlineKeyboardMarkup:
        def button(label, action):
            return InlineKeyboardButton(text=label, callback_data=f"mb:{self.token}:{action}")

        start = self.page * PAGE_SIZE
        rows = []
        for index, (_, name) in enumerate(self.items[start:start + PAGE_SIZE], start):
            mark = "✅" if index in self.selected else "❌"
            rows.append([button(f"{mark} {name[:85]}", f"t{index}")])
        nav = []
        if self.page:
            nav.append(button("←", f"p{self.page - 1}"))
        if start + PAGE_SIZE < len(self.items):
            nav.append(button("→", f"p{self.page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([button(f"🚀 Буст выбранных ({len(self.selected)})", "buy")])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def text(self) -> str:
        pages = (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE
        mode = "🧪 Тестовый режим, без оплаты." if self.dry_run else "Премиум оплачивается с баланса Playerok."
        return f"Выберите объявления для буста. {mode}\nСтраница {self.page + 1} из {pages}"


# One current selection per Telegram user. Restarting invalidates old buttons.
selections: dict[int, Selection] = {}


@router.callback_query(F.data == "menu:boost")
async def open_boost(callback: CallbackQuery, ctx: AppContext, user: User | None):
    if not await require_automation_cb(callback, user):
        return
    await safe_answer(callback)
    await callback.message.edit_text("Загружаю объявления Playerok…")
    try:
        async with ctx.automation.manual_session() as (page, seller):
            items = await ctx.client.active_listings(page, seller)
        settings = await ctx.settings.get()
    except Exception as exc:
        logger.exception("Cannot load listings for manual boost")
        reason = str(exc) if isinstance(exc, PlayerokError) else "Попробуйте ещё раз."
        await callback.message.edit_text(f"Не удалось загрузить объявления. {reason}", parse_mode=None, reply_markup=back_menu())
        return
    if not items:
        await callback.message.edit_text("Сейчас нет объявлений в продаже.", reply_markup=back_menu())
        return
    selection = Selection(seller, list(items.items()), settings.dry_run)
    selections[callback.from_user.id] = selection
    await callback.message.edit_text(selection.text(), reply_markup=selection.keyboard())


@router.callback_query(F.data.startswith("mb:"))
async def select_boost(callback: CallbackQuery, ctx: AppContext, user: User | None):
    if not await require_automation_cb(callback, user):
        return
    owner = callback.from_user.id
    selection = selections.get(owner)
    _, _, payload = callback.data.partition(":")
    token, _, action = payload.partition(":")
    if selection is None or token != selection.token:
        await safe_answer(callback, "Откройте «🚀 Буст» заново.")
        return

    async with selection.lock:
        if selections.get(owner) is not selection:
            await safe_answer(callback, "Этот выбор уже закрыт.")
            return
        if action == "buy":
            if not selection.selected:
                await safe_answer(callback, "Сначала выберите объявления.")
                return
            del selections[owner]
        else:
            index = action[1:]
            if not index.isdecimal() or int(index) >= len(selection.items):
                await safe_answer(callback, "Неизвестное объявление.")
                return
            if action.startswith("t"):
                selection.selected.symmetric_difference_update({int(index)})
            elif action.startswith("p") and int(index) * PAGE_SIZE < len(selection.items):
                selection.page = int(index)
            else:
                await safe_answer(callback)
                return
            await safe_answer(callback)
            await callback.message.edit_text(selection.text(), reply_markup=selection.keyboard())
            return

    await safe_answer(callback)
    selected = [selection.items[i] for i in sorted(selection.selected)]
    await callback.message.edit_text(f"Проверяю выбранные объявления ({len(selected)})…")
    results = []
    try:
        async for result in ctx.automation.boost_selected(selection.seller, selected, selection.dry_run, user):
            results.append(result[:400])
    except Exception as exc:
        logger.exception("Manual boost interrupted")
        reason = str(exc) if isinstance(exc, PlayerokError) else "Проверьте последнюю операцию в Playerok."
        results.append(f"Остановлено после {len(results)} из {len(selected)}. {reason}")
    # Keep each Telegram message below its text length limit.
    for offset in range(0, len(results), 5):
        chunk = "\n".join(results[offset:offset + 5])
        send = callback.message.answer if offset else callback.message.edit_text
        await send(chunk, parse_mode=None, reply_markup=back_menu() if offset + 5 >= len(results) else None)
