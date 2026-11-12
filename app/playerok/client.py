from __future__ import annotations

import asyncio
import logging
import random
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Callable

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.config import AppConfig
from app.playerok.exceptions import (
    CaptchaError,
    DailyLimitError,
    LayoutChangedError,
    PaymentUncertainError,
    PlayerokError,
    RateLimitError,
    SessionExpiredError,
    StopRequestedError,
)
from app.playerok.selectors import Selectors
from app.utils.parsing import money, parse_money, truncate


logger = logging.getLogger(__name__)


VIEWER_QUERY = """
query BotViewer {
  viewer {
    id
    username
    hasFrozenBalance
  }
}
""".strip()

SALES_QUERY = """
query BotSales($filter: ItemDealFilter!, $pagination: Pagination) {
  deals(filter: $filter, pagination: $pagination) {
    edges {
      node {
        id
        status
        direction
        createdAt
        completedAt
        item {
          id
          slug
          name
          price
          rawPrice
          priority
          status
        }
      }
    }
    totalCount
  }
}
""".strip()

# Playerok publishes new listings with this mutation. Only the fields the bot
# needs are requested, the server returns exactly what the query asks for.
CREATE_ITEM_QUERY = """
mutation createItem($input: CreateItemInput!, $attachments: [Upload!]) {
  createItem(input: $input, attachments: $attachments) {
    id
    slug
    name
    price
    status
    priority
    __typename
  }
}
""".strip()

# Playerok publishes a listing (a draft) with this mutation. The premium
# variant additionally buys the priority, the free one uses the standard
# "Обычный" status.
PUBLISH_ITEM_QUERY = """
mutation publishItem($input: PublishItemInput!) {
  publishItem(input: $input) {
    id
    slug
    status
    priority
    __typename
  }
}
""".strip()


@dataclass(slots=True)
class SessionInfo:
    username: str | None
    has_frozen_balance: bool = False
    user_id: str | None = None


@dataclass(slots=True)
class SaleInfo:
    external_id: str
    title: str | None
    price: Decimal | None
    deal_url: str
    lot_url: str | None
    item_slug: str | None
    status: str
    created_at: datetime | None = None


@dataclass(slots=True)
class ChatInfo:
    chat_id: str
    chat_type: str
    unread_count: int
    last_message_id: str | None
    last_message_at: datetime | None
    sender_username: str | None
    sender_id: str | None
    is_read: bool


@dataclass(slots=True)
class ItemInfo:
    """Full data of a Playerok listing, used to publish an identical copy."""

    id: str
    slug: str
    name: str
    price: Decimal | None
    description: str
    attributes: dict[str, Any]
    game_category_id: str
    obtaining_type_id: str
    data_fields: list[dict[str, str]]
    attachment_urls: list[str]
    priority: str | None = None
    status: str | None = None
    # Price without the seller's discount (rawPrice). Used as a safe fallback
    # when the boost price cannot be read.
    raw_price: Decimal | None = None


@dataclass(slots=True)
class ListingResult:
    slug: str | None
    lot_url: str | None
    item_id: str | None
    price: Decimal | None
    dry_run: bool = False


@dataclass(slots=True)
class PaymentInspection:
    amount: Decimal | None
    source: str  # main | frozen | other | unknown
    source_text: str
    dialog_text: str


@dataclass(slots=True)
class BoostOutcome:
    status: str  # success | dry_run | deferred | limit
    amount: Decimal | None = None
    source: str = "unknown"
    reason: str | None = None


class PlayerokClient:
    """Playwright client. Cookies are only kept in memory for one browser context."""

    def __init__(self, config: AppConfig, selectors: Selectors) -> None:
        self.config = config
        self.selectors = selectors
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()
        self._stop_check: Callable[[], bool] = lambda: False

    def set_stop_check(self, callback: Callable[[], bool]) -> None:
        self._stop_check = callback

    async def start(self) -> None:
        async with self._browser_lock:
            if self._browser is not None and self._browser.is_connected():
                return
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=self.config.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Cannot start Chromium")
                raise PlayerokError(
                    "Не удалось запустить Chromium. Проверьте установку Playwright."
                ) from exc

    async def close(self) -> None:
        async with self._browser_lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._playwright = None

    @asynccontextmanager
    async def page_session(
        self, cookies: list[dict[str, Any]]
    ) -> AsyncIterator[Page]:
        await self.start()
        if self._browser is None:
            raise PlayerokError("Браузер не запущен")
        context = await self._browser.new_context(
            locale="ru-RU",
            timezone_id=self.config.timezone,
            user_agent=self.config.user_agent,
            viewport={"width": 1366, "height": 900},
            device_scale_factor=1,
        )
        try:
            await context.add_cookies(cookies)
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()
            page.set_default_timeout(self.config.browser_timeout_ms)
            page.set_default_navigation_timeout(self.config.navigation_timeout_ms)
            page.on("dialog", self._handle_dialog)
            yield page
        finally:
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _handle_dialog(dialog) -> None:  # noqa: ANN001
        try:
            if dialog.type == "beforeunload":
                await dialog.dismiss()
            else:
                await dialog.accept()
        except Exception:  # noqa: BLE001
            pass

    async def verify_cookies(
        self, cookies: list[dict[str, Any]]
    ) -> SessionInfo:
        async with self.page_session(cookies) as page:
            return await self.check_session(page)

    async def check_session(self, page: Page) -> SessionInfo:
        auth_path = self.selectors.url("auth_check")
        response = await self._goto(page, self._absolute(auth_path))

        body = await self._body_text(page)
        challenge = self._is_challenge(body)
        if challenge:
            # The anti-bot page answers 403 as well; that is not an expired
            # session. The page verifies itself and the bot waits for it.
            logger.info("Playerok is still verifying the browser, waiting")
            await page.wait_for_timeout(
                max(5, self.config.challenge_wait_seconds) * 1000
            )
            response = await self._goto(page, self._absolute(auth_path))
            body = await self._body_text(page)
            challenge = self._is_challenge(body)
            if challenge:
                raise CaptchaError(
                    "Playerok проверяет браузер и не пропускает автоматизацию"
                )

        login_form = await self._find_visible(
            page, "session.login_form", timeout_ms=1200
        )
        if not challenge and (
            (response is not None and response.status in (401, 403))
            or "/profile/auth" in page.url
            or login_form is not None
            or "вход или регистрация" in body.lower()
        ):
            raise SessionExpiredError("Сессия Playerok истекла")

        viewer = await self._try_viewer(page)
        username = (
            self.config.playerok_username
            or (str(viewer.get("username")) if viewer and viewer.get("username") else None)
            or await self._username_from_page(page)
        )
        if not username:
            raise LayoutChangedError(
                "Не удалось определить username Playerok. "
                "Укажите PLAYEROK_USERNAME в .env."
            )
        return SessionInfo(
            username=username,
            has_frozen_balance=bool(viewer.get("hasFrozenBalance")) if viewer else False,
            user_id=(
                str(viewer.get("id"))
                if viewer and viewer.get("id")
                else None
            ),
        )

    async def fetch_sales(
        self,
        page: Page,
        username: str,
        user_id: str | None = None,
    ) -> list[SaleInfo]:
        if bool(self.selectors.get("sales.use_graphql", True)):
            try:
                return await self._fetch_sales_graphql(page, username, user_id)
            except (SessionExpiredError, RateLimitError, CaptchaError):
                raise
            except PlayerokError as exc:
                logger.warning("GraphQL sales failed, trying DOM fallback: %s", exc)
        return await self._fetch_sales_dom(page, username)

    async def _wait_for_item(
        self, page: Page, slug: str, timeout_ms: int = 90000
    ) -> ItemInfo | None:
        """Wait until a freshly created listing becomes available.

        Playerok writes new items to the database asynchronously, so the item
        query answers "not found" for a short while after createItem.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                return await self.fetch_item(page, slug)
            except PlayerokError as exc:
                last_error = exc
                await page.wait_for_timeout(3000)
        if last_error is not None:
            logger.warning("Listing %s is not available yet: %s", slug, last_error)
        return None

    def skip_boost_reason(self, title: str | None) -> str | None:
        """Return the trigger phrase that forbids boosting this title.

        The listing is still published, only without premium.
        """
        if not title:
            return None
        lowered = title.casefold()
        for phrase in self.selectors.strings("boost.skip_titles"):
            needle = str(phrase).strip().casefold()
            if not needle:
                continue
            if needle in lowered:
                return str(phrase).strip()
        return None

    async def fetch_item(self, page: Page, slug: str) -> ItemInfo:
        """Read the full listing data (needed to create an identical copy)."""
        operation = str(self.selectors.get("graphql.item_operation", "item"))
        sha256_hash = str(self.selectors.get("graphql.item_sha256", "") or "")
        if not sha256_hash:
            raise LayoutChangedError("Не задан graphql.item_sha256 в selectors.yaml")
        data = await self._graphql_persisted(
            page,
            operation,
            {
                "slug": slug,
                "hasSupportAccess": False,
                "showForbiddenImage": True,
            },
            sha256_hash,
        )
        item = data.get("item")
        if not isinstance(item, dict):
            raise LayoutChangedError("Ответ GraphQL не содержит item")
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        obtaining = (
            item.get("obtainingType")
            if isinstance(item.get("obtainingType"), dict)
            else {}
        )
        attributes = (
            item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        )
        data_fields: list[dict[str, str]] = []
        for field in item.get("dataFields") or []:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("id") or "").strip()
            value = field.get("value")
            if not field_id or value is None:
                continue
            data_fields.append({"fieldId": field_id, "value": str(value)})
        attachments: list[str] = []
        for attachment in item.get("attachments") or []:
            if isinstance(attachment, dict):
                url = str(attachment.get("url") or "").strip()
                if url:
                    attachments.append(url)
        return ItemInfo(
            id=str(item.get("id") or ""),
            slug=str(item.get("slug") or slug),
            name=str(item.get("name") or ""),
            price=parse_money(item.get("price") or item.get("rawPrice")),
            description=str(item.get("description") or ""),
            attributes=attributes,
            game_category_id=str(category.get("id") or ""),
            obtaining_type_id=str(obtaining.get("id") or ""),
            data_fields=data_fields,
            attachment_urls=attachments,
            priority=str(item.get("priority") or "") or None,
            status=str(item.get("status") or "") or None,
            raw_price=parse_money(item.get("rawPrice")),
        )

    async def create_listing(
        self,
        page: Page,
        item: ItemInfo,
        *,
        dry_run: bool,
    ) -> ListingResult:
        """Create a brand new listing that is an exact copy of ``item``.

        The Playerok button "Выставить повторно" only reactivates the sold
        item, so the bot publishes a new listing with the same title,
        description, pictures, price and characteristics instead.
        """
        if dry_run:
            return ListingResult(
                slug=None,
                lot_url=None,
                item_id=None,
                price=item.price,
                dry_run=True,
            )
        if not item.game_category_id or not item.obtaining_type_id:
            raise LayoutChangedError(
                "Не удалось определить категорию товара для копирования"
            )
        if item.price is None or item.price <= 0:
            raise LayoutChangedError("Не удалось определить цену товара")

        await self._before_action()
        if item.slug:
            await self._goto(page, self._absolute(self.selectors.url("item", slug=item.slug)))

        images: list[tuple[str, str]] = []
        for url in item.attachment_urls[:3]:
            try:
                response = await page.context.request.get(url)
                if not response.ok:
                    continue
                body = await response.body()
                if not body:
                    continue
                import base64

                images.append(
                    (
                        base64.b64encode(body).decode("ascii"),
                        response.headers.get("content-type", "image/jpeg"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Cannot download item picture %s: %s", url, exc)

        payload = {
            "input": {
                "gameCategoryId": item.game_category_id,
                "obtainingTypeId": item.obtaining_type_id,
                "attributes": item.attributes,
                "name": item.name,
                "price": int(item.price),
                "description": item.description,
                "dataFields": item.data_fields,
            },
            "images": images,
        }
        result = await self._create_item_mutation(page, payload)
        created = result.get("createItem")
        if not isinstance(created, dict):
            raise LayoutChangedError("Playerok не вернул созданный товар")
        slug = str(created.get("slug") or "").strip()
        return ListingResult(
            slug=slug or None,
            lot_url=(
                self._absolute(self.selectors.url("item", slug=slug)) if slug else None
            ),
            item_id=str(created.get("id") or "") or None,
            price=parse_money(created.get("price")),
            dry_run=False,
        )

    async def _create_item_mutation(
        self, page: Page, payload: dict[str, Any]
    ) -> dict[str, Any]:
        images: list[tuple[str, str]] = payload.get("images") or []
        operations = {
            "operationName": "createItem",
            "variables": {
                "input": payload["input"],
                "attachments": [None] * len(images),
            },
            "query": CREATE_ITEM_QUERY,
        }
        map_data = {
            str(index + 1): [f"variables.attachments.{index}"]
            for index in range(len(images))
        }
        endpoint = str(self.selectors.get("graphql.endpoint", "/graphql"))
        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, operations, map, images}) => {
              const form = new FormData();
              form.append('operations', JSON.stringify(operations));
              form.append('map', JSON.stringify(map));
              for (let index = 0; index < images.length; index++) {
                const [base64, mime] = images[index];
                const binary = atob(base64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                form.append(
                  String(index + 1),
                  new File([bytes], 'item' + (index + 1) + '.jpg', {type: mime || 'image/jpeg'})
                );
              }
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint, {
                signal: controller.signal,
                method: 'POST',
                credentials: 'include',
                headers: {
                  'apollo-require-preflight': 'true',
                  'x-apollo-operation-name': 'createItem',
                },
                body: form,
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "operations": operations,
                "map": map_data,
                "images": images,
            },
        )
        status = int(result.get("status") or 0)
        if status in (401, 403):
            raise SessionExpiredError("GraphQL: требуется авторизация")
        if status == 429:
            raise RateLimitError("GraphQL: 429")
        payload_json = result.get("json")
        if not isinstance(payload_json, dict):
            raise PlayerokError(
                f"Playerok не принял новый товар (HTTP {status}): "
                f"{truncate(str(result.get('text') or ''), 300)}"
            )
        errors = payload_json.get("errors")
        if errors:
            messages = " ".join(
                str(error.get("message", ""))
                for error in errors
                if isinstance(error, dict)
            )
            lowered = messages.lower()
            if any(word in lowered for word in ("unauthorized", "unauthenticated", "auth")):
                raise SessionExpiredError("GraphQL: сессия истекла")
            if "too many" in lowered or "rate limit" in lowered:
                raise RateLimitError("GraphQL: слишком много запросов")
            raise PlayerokError(f"Не удалось создать товар: {truncate(messages, 500)}")
        data = payload_json.get("data")
        if not isinstance(data, dict):
            raise PlayerokError("Playerok вернул пустой ответ на создание товара")
        return data

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        """Close the cookie banner so it cannot cover the action buttons."""
        for label in ("Хорошо", "ОК", "Принять"):
            try:
                button = page.get_by_role("button", name=label, exact=True)
                if not await button.count():
                    continue
                candidate = button.first
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=3000)
                await page.wait_for_timeout(600)
                return
            except Exception:  # noqa: BLE001
                continue

    async def apply_boost(
        self,
        page: Page,
        lot_url: str,
        *,
        dry_run: bool,
        remaining_budget: Decimal,
        max_amount: Decimal | None = None,
        item_full_price: Decimal | None = None,
    ) -> BoostOutcome:
        await self._before_action()
        await self._goto(page, lot_url)
        await self._dismiss_cookie_banner(page)

        # 1. Open the promotion screen ("Выставить" / "Повысить статус" /
        #    "Поднять в топ").
        entry = await self._find_visible(page, "boost.entry_buttons", timeout_ms=15000)
        if entry is None:
            # A freshly created listing may need a moment before its page
            # shows the seller controls.
            slug = lot_url.rstrip("/").split("/")[-1]
            if slug:
                await self._wait_for_item(page, slug, timeout_ms=60000)
                await self._goto(page, lot_url)
                entry = await self._find_visible(
                    page, "boost.entry_buttons", timeout_ms=15000
                )
        if entry is None:
            raise LayoutChangedError(
                "Кнопка буста/премиума не найдена. Обновите boost.entry_buttons "
                "в selectors.yaml."
            )
        await self._before_action()
        await self._safe_click(entry)
        await page.wait_for_timeout(1200)

        # 2. Make sure the "Премиум" option is selected in the modal.
        await self._ensure_premium_option(page)

        # 3. Confirm the promotion. The button contains the price, e.g.
        #    "Повысить до Премиум за 99 ₽ 149".
        confirm = await self._find_visible(
            page, "boost.confirm_buttons", timeout_ms=4000
        )
        if confirm is None:
            raise LayoutChangedError(
                "Кнопка подтверждения премиума не найдена. Проверьте "
                "boost.confirm_buttons в selectors.yaml."
            )
        confirm_text = await self._inner_text(confirm)
        premium_price = self._parse_premium_price(confirm_text)

        # Per-boost limit. The decision is based on the price of the boost
        # itself. If the boost price cannot be read, the item price without
        # the seller's discount is used instead, so a cheap discounted item
        # never bypasses the limit.
        effective_price = premium_price if premium_price is not None else item_full_price
        if effective_price is None:
            await self._close_dialog(page, None)
            return BoostOutcome(
                status="deferred",
                amount=None,
                source="unknown",
                reason=(
                    "Не удалось определить стоимость буста, поэтому оплата "
                    "не выполнялась"
                ),
            )
        if max_amount is not None and effective_price > max_amount:
            await self._close_dialog(page, None)
            return BoostOutcome(
                status="skipped",
                amount=effective_price,
                source="unknown",
                reason=(
                    f"Стоимость буста {money(effective_price)} выше лимита "
                    f"за один буст ({money(max_amount)})"
                ),
            )

        await self._before_action()
        await self._safe_click(confirm)
        await page.wait_for_timeout(1500)

        # 4. Payment window.
        dialog = await self._payment_dialog(page)
        if dialog is None:
            raise LayoutChangedError(
                "Платёжное окно буста не найдено. Проверьте payment.dialog "
                "и boost.confirm_buttons в selectors.yaml."
            )

        inspection = await self._inspect_payment(dialog)
        if premium_price is not None and inspection.amount is None:
            inspection.amount = premium_price

        # The window may show a different amount than the promotion screen.
        if max_amount is not None and inspection.amount is not None:
            if inspection.amount > max_amount:
                await self._close_dialog(page, dialog)
                return BoostOutcome(
                    status="limit",
                    amount=inspection.amount,
                    source=inspection.source,
                    reason=(
                        f"Стоимость буста {money(inspection.amount)} выше лимита "
                        f"за один буст ({money(max_amount)})"
                    ),
                )

        # Payment safety: never pay from anything except the main balance.
        # This check runs in dry-run too, so the operator sees the real outcome.
        if inspection.source != "main":
            await self._close_dialog(page, dialog)
            prefix = "DRY-RUN: оплата не выполнялась. " if dry_run else ""
            return BoostOutcome(
                status="deferred",
                amount=inspection.amount,
                source=inspection.source,
                reason=(
                    prefix
                    + "Источник списания не основной баланс: "
                    + (inspection.source_text or inspection.source)
                ),
            )

        if dry_run:
            await self._close_dialog(page, dialog)
            return BoostOutcome(
                status="dry_run",
                amount=inspection.amount,
                source=inspection.source,
                reason=(
                    "DRY-RUN: оплата не выполнялась. Источник: "
                    f"{inspection.source_text or 'не определён'}"
                ),
            )

        if inspection.amount is None:
            await self._close_dialog(page, dialog)
            return BoostOutcome(
                status="deferred",
                source=inspection.source,
                reason="Не удалось определить стоимость буста",
            )
        if inspection.amount > remaining_budget:
            await self._close_dialog(page, dialog)
            return BoostOutcome(
                status="limit",
                amount=inspection.amount,
                source=inspection.source,
                reason="Дневной лимит буста исчерпан",
            )

        pay = await self._find_visible(
            dialog, "payment.pay_buttons", timeout_ms=2000
        )
        if pay is None:
            await self._close_dialog(page, dialog)
            raise LayoutChangedError(
                "Кнопка оплаты буста не найдена. Проверьте payment.pay_buttons."
            )

        await self._before_action()
        await self._safe_click(pay)
        await self._wait_for_texts(page, "payment.success_texts", timeout_ms=8000)
        dialog_gone = await self._dialog_gone(page, dialog)
        if not dialog_gone:
            await page.wait_for_timeout(1200)
            dialog_gone = await self._dialog_gone(page, dialog)
        if not dialog_gone:
            raise PaymentUncertainError(
                "Оплата буста отправлена, но платёжное окно не закрылось. "
                "Проверьте аккаунт вручную; автобуст остановлен."
            )

        error_texts = [
            text.lower()
            for text in self.selectors.strings("payment.error_texts")
            if text
        ]
        if error_texts:
            body = (await self._body_text(page)).lower()
            if any(text in body for text in error_texts):
                raise PaymentUncertainError(
                    "После оплаты появилось сообщение об ошибке. "
                    "Проверьте аккаунт вручную; автобуст остановлен."
                )

        return BoostOutcome(
            status="success",
            amount=inspection.amount,
            source=inspection.source,
            reason=None,
        )

    async def fetch_items_count(self, page: Page, user_id: str) -> int | None:
        """Return the number of the seller's listings that are on sale."""
        operation = str(self.selectors.get("graphql.items_operation", "items"))
        sha256_hash = str(self.selectors.get("graphql.items_sha256", "") or "")
        if not sha256_hash:
            return None
        statuses = self.selectors.strings("graphql.items_active_statuses") or [
            "APPROVED"
        ]
        page_size = int(self.selectors.get("graphql.items_page_size", 16))
        # Playerok rejects page sizes above 16 for this persisted query.
        page_size = max(1, min(page_size, 16))
        data = await self._graphql_persisted(
            page,
            operation,
            {
                "pagination": {"first": page_size},
                "filter": {
                    "userId": user_id,
                    "status": list(statuses),
                    "withOfficial": False,
                },
                "showForbiddenImage": True,
            },
            sha256_hash,
        )
        items = data.get("items")
        if not isinstance(items, dict):
            return None
        total = items.get("totalCount")
        try:
            return int(total) if total is not None else None
        except (TypeError, ValueError):
            return None

    async def fetch_chats(
        self, page: Page, user_id: str
    ) -> list[ChatInfo]:
        """Return the latest chat list with unread counters and senders."""
        operation = str(
            self.selectors.get("graphql.chats_operation", "userChats")
        )
        sha256_hash = str(
            self.selectors.get("graphql.chats_sha256", "") or ""
        )
        if not sha256_hash:
            raise LayoutChangedError(
                "Не задан graphql.chats_sha256 в selectors.yaml"
            )
        page_size = int(self.selectors.get("graphql.chats_page_size", 50))
        data = await self._graphql_persisted(
            page,
            operation,
            {
                "pagination": {"first": max(1, min(page_size, 100))},
                "filter": {"userId": user_id},
            },
            sha256_hash,
        )
        chats = data.get("chats")
        if not isinstance(chats, dict):
            raise LayoutChangedError("Ответ GraphQL не содержит chats")
        edges = chats.get("edges")
        if not isinstance(edges, list):
            raise LayoutChangedError("Ответ GraphQL не содержит chats.edges")

        result: list[ChatInfo] = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            chat_id = str(node.get("id") or "").strip()
            if not chat_id:
                continue
            last = node.get("lastMessage")
            if not isinstance(last, dict):
                last = {}
            sender = last.get("user")
            if not isinstance(sender, dict):
                sender = {}
            result.append(
                ChatInfo(
                    chat_id=chat_id,
                    chat_type=str(node.get("type") or ""),
                    unread_count=int(node.get("unreadMessagesCounter") or 0),
                    last_message_id=(
                        str(last.get("id") or "").strip() or None
                    ),
                    last_message_at=self._parse_dt(last.get("createdAt")),
                    sender_username=(
                        str(sender.get("username") or "").strip() or None
                    ),
                    sender_id=str(sender.get("id") or "").strip() or None,
                    is_read=bool(last.get("isRead")),
                )
            )
        return result

    async def _fetch_sales_graphql(
        self,
        page: Page,
        username: str,
        user_id: str | None = None,
    ) -> list[SaleInfo]:
        page_size = int(self.selectors.get("graphql.sales_page_size", 50))
        operation = str(
            self.selectors.get("graphql.deals_operation", "deals")
        )
        sha256_hash = str(
            self.selectors.get("graphql.deals_sha256", "") or ""
        )
        first = max(1, min(page_size, 100))

        if user_id and sha256_hash:
            # Playerok serves the sales list through an Apollo persisted query.
            # A raw POST with the same fields returns HTTP 500, so use the
            # exact operation/hash the site itself uses.
            data = await self._graphql_persisted(
                page,
                operation,
                {
                    "pagination": {"first": first},
                    "filter": {
                        "userId": user_id,
                        "direction": "OUT",
                        "status": ["PAID"],
                    },
                    "showForbiddenImage": True,
                },
                sha256_hash,
            )
        else:
            data = await self._graphql(
                page,
                SALES_QUERY,
                {
                    "filter": {"direction": "OUT"},
                    "pagination": {"first": first},
                },
                operation_name="BotSales",
            )
        deals = data.get("deals")
        if not isinstance(deals, dict):
            raise LayoutChangedError("Ответ GraphQL не содержит deals")
        edges = deals.get("edges")
        if not isinstance(edges, list):
            raise LayoutChangedError("Ответ GraphQL не содержит deals.edges")

        completed = {
            str(item).upper()
            for item in self.selectors.strings("graphql.completed_sale_statuses")
        }
        sales: list[SaleInfo] = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            status = str(node.get("status") or "").upper()
            if completed and status not in completed:
                continue
            direction = str(node.get("direction") or "").upper()
            if direction and direction != "OUT":
                continue
            external_id = str(node.get("id") or "").strip()
            if not external_id:
                continue
            item = node.get("item") if isinstance(node.get("item"), dict) else {}
            slug = str(item.get("slug") or "").strip() or None
            deal_url = self._absolute(self.selectors.url("deal", id=external_id))
            lot_url = (
                self._absolute(self.selectors.url("item", slug=slug)) if slug else None
            )
            created_at = self._parse_dt(node.get("createdAt") or node.get("completedAt"))
            sales.append(
                SaleInfo(
                    external_id=external_id,
                    title=(str(item.get("name"))[:500] if item.get("name") else None),
                    price=parse_money(item.get("price") or item.get("rawPrice")),
                    deal_url=deal_url,
                    lot_url=lot_url,
                    item_slug=slug,
                    status=status or "CONFIRMED",
                    created_at=created_at,
                )
            )
        sales.sort(key=lambda item: item.created_at or datetime.min)
        return sales

    async def _fetch_sales_dom(
        self, page: Page, username: str
    ) -> list[SaleInfo]:
        sales_url = self._absolute(self.selectors.url("sales", username=username))
        await self._goto(page, sales_url)
        await page.wait_for_timeout(1200)

        cards = await self._find_all(page, "sales.cards")
        if cards is None:
            empty = await self._find_visible(page, "sales.empty_markers", timeout_ms=800)
            if empty is not None:
                return []
            raise LayoutChangedError(
                "Список продаж не найден. Проверьте sales.cards в selectors.yaml "
                "или включите sales.use_graphql."
            )

        completed_markers = [
            marker.lower() for marker in self.selectors.strings("sales.completed_status_markers")
        ]
        url_attr = str(self.selectors.get("sales.card_url_attribute", "href"))
        count = min(await cards.count(), 100)
        result: list[SaleInfo] = []
        for index in range(count):
            card = cards.nth(index)
            href = await card.get_attribute(url_attr)
            if not href:
                continue
            match = re.search(r"/deal/([^/?#]+)", href)
            if not match:
                continue
            text = await self._inner_text(card)
            status_text = await self._relative_text(card, "sales.status")
            combined = f"{status_text} {text}".lower()
            if completed_markers and not any(marker in combined for marker in completed_markers):
                continue
            title = await self._relative_text(card, "sales.title")
            if not title:
                title = text.splitlines()[0] if text else None
            price = parse_money(await self._relative_text(card, "sales.price"))
            slug = None
            links = card.locator('a[href*="/products/"]')
            if await links.count():
                item_href = await links.first.get_attribute("href")
                if item_href:
                    slug_match = re.search(r"/products/([^/?#]+)", item_href)
                    slug = slug_match.group(1) if slug_match else None
            external_id = match.group(1)
            result.append(
                SaleInfo(
                    external_id=external_id,
                    title=truncate(title, 500) if title else None,
                    price=price,
                    deal_url=self._absolute(href),
                    lot_url=(
                        self._absolute(self.selectors.url("item", slug=slug))
                        if slug
                        else None
                    ),
                    item_slug=slug,
                    status="DOM",
                )
            )
        return result

    async def _try_viewer(self, page: Page) -> dict[str, Any] | None:
        try:
            data = await self._graphql(
                page, VIEWER_QUERY, operation_name="BotViewer"
            )
        except (SessionExpiredError, RateLimitError):
            raise
        except PlayerokError as exc:
            logger.info("Viewer GraphQL unavailable, using DOM fallback: %s", exc)
            return None
        viewer = data.get("viewer")
        return viewer if isinstance(viewer, dict) else None

    async def _ensure_origin(self, page: Page) -> None:
        """Make sure the page is on playerok.com before a relative fetch."""
        if "playerok.com" in (page.url or ""):
            return
        await self._goto(page, self._absolute("/profile"))

    async def _run_graphql_fetch(
        self, page: Page, script: str, argument: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a GraphQL fetch and wait out the anti-bot challenge if needed.

        Playerok answers 401/403 on the API while its browser verification
        page is shown. The verification resolves itself, so the call is
        repeated once after the wait.
        """
        result = await self._evaluate_with_retry(page, script, argument)
        status = int(result.get("status") or 0)
        if status in (401, 403):
            logger.info("GraphQL got %s, waiting for the browser check", status)
            await self._wait_for_challenge(page)
            result = await self._evaluate_with_retry(page, script, argument)
        return result

    async def _evaluate_with_retry(
        self, page: Page, script: str, argument: dict[str, Any]
    ) -> Any:
        """Evaluate JS, retrying once if the page navigated meanwhile."""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await asyncio.wait_for(
                    page.evaluate(script, argument),
                    timeout=self.config.browser_timeout_ms / 1000 + 30,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                message = str(exc)
                if (
                    "Execution context was destroyed" not in message
                    and "TimeoutError" not in message
                    and "timed out" not in message.lower()
                ):
                    raise
                if "TimeoutError" in message or "timed out" in message.lower():
                    logger.warning("Page evaluate timed out, retrying")
                logger.info("GraphQL call interrupted by navigation, retrying")
                await page.wait_for_timeout(1200)
                await self._ensure_origin(page)
        raise last_error if last_error else PlayerokError("GraphQL: evaluate failed")

    async def _graphql(
        self,
        page: Page,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        endpoint = str(self.selectors.get("graphql.endpoint", "/graphql"))
        if not operation_name:
            match = re.search(
                r"\bquery\s+([A-Za-z_][A-Za-z0-9_]*)", query
            )
            operation_name = match.group(1) if match else None
        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, query, variables, operationName}) => {
              const headers = {'content-type': 'application/json'};
              if (operationName) {
                headers['apollo-require-preflight'] = 'true';
                headers['x-apollo-operation-name'] = operationName;
              }
              const body = {query, variables};
              if (operationName) body.operationName = operationName;
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint, {
                signal: controller.signal,
                method: 'POST',
                credentials: 'include',
                headers,
                body: JSON.stringify(body)
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "query": query,
                "variables": variables or {},
                "operationName": operation_name or "",
            },
        )
        status = int(result.get("status") or 0)
        if status in (401, 403):
            raise SessionExpiredError("GraphQL: требуется авторизация")
        if status == 429:
            raise RateLimitError("GraphQL: 429")
        payload = result.get("json")
        if not isinstance(payload, dict):
            detail = truncate(str(result.get("text") or ""), 200)
            raise PlayerokError(
                f"GraphQL: некорректный ответ HTTP {status}"
                + (f" ({detail})" if detail else "")
            )
        errors = payload.get("errors")
        if errors:
            messages = " ".join(
                str(error.get("message", ""))
                for error in errors
                if isinstance(error, dict)
            )
            lowered = messages.lower()
            if any(word in lowered for word in ("unauthorized", "unauthenticated", "auth")):
                raise SessionExpiredError("GraphQL: сессия истекла")
            if "too many" in lowered or "rate limit" in lowered:
                raise RateLimitError("GraphQL: слишком много запросов")
            raise PlayerokError(f"GraphQL: {truncate(messages, 500)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlayerokError("GraphQL: пустой ответ")
        return data

    async def _graphql_persisted(
        self,
        page: Page,
        operation_name: str,
        variables: dict[str, Any],
        sha256_hash: str,
    ) -> dict[str, Any]:
        """Run an Apollo persisted query exactly as the Playerok frontend does."""
        await self._ensure_origin(page)
        endpoint = str(self.selectors.get("graphql.endpoint", "/graphql"))
        result = await self._run_graphql_fetch(
            page,
            """
            async ({endpoint, operationName, variables, sha256Hash}) => {
              const params = new URLSearchParams({
                operationName,
                variables: JSON.stringify(variables),
                extensions: JSON.stringify({
                  persistedQuery: {version: 1, sha256Hash}
                })
              });
              const controller = new AbortController();
              const timer = setTimeout(() => controller.abort(), 30000);
              let response;
              try {
                response = await fetch(endpoint + '?' + params.toString(), {
                signal: controller.signal,
                method: 'GET',
                credentials: 'include',
                headers: {
                  'apollo-require-preflight': 'true',
                  'x-apollo-operation-name': operationName
                }
              });
              } catch (e) {
                clearTimeout(timer);
                return {status: 0, text: 'fetch failed: ' + String(e), json: null};
              }
              clearTimeout(timer);
              const text = await response.text();
              let json = null;
              try { json = JSON.parse(text); } catch (e) { json = null; }
              return {status: response.status, text: text.slice(0, 4000), json};
            }
            """,
            {
                "endpoint": endpoint,
                "operationName": operation_name,
                "variables": variables,
                "sha256Hash": sha256_hash,
            },
        )
        status = int(result.get("status") or 0)
        if status in (401, 403):
            raise SessionExpiredError("GraphQL: требуется авторизация")
        if status == 429:
            raise RateLimitError("GraphQL: 429")
        payload = result.get("json")
        if not isinstance(payload, dict):
            detail = truncate(str(result.get("text") or ""), 200)
            raise PlayerokError(
                f"GraphQL: некорректный ответ HTTP {status}"
                + (f" ({detail})" if detail else "")
            )
        errors = payload.get("errors")
        if errors:
            messages = " ".join(
                str(error.get("message", ""))
                for error in errors
                if isinstance(error, dict)
            )
            lowered = messages.lower()
            if any(
                word in lowered
                for word in ("unauthorized", "unauthenticated", "auth")
            ):
                raise SessionExpiredError("GraphQL: сессия истекла")
            if "too many" in lowered or "rate limit" in lowered:
                raise RateLimitError("GraphQL: слишком много запросов")
            raise PlayerokError(f"GraphQL: {truncate(messages, 500)}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlayerokError("GraphQL: пустой ответ")
        return data

    async def publish_listing(self, page: Page, lot_url: str) -> bool:
        """Publish a fresh listing without premium (the free option).

        Used when auto-boost is off or when the single-boost limit is
        exceeded: the listing still goes on sale, only without premium.
        """
        await self._before_action()
        await self._goto(page, lot_url)
        await self._dismiss_cookie_banner(page)

        slug = lot_url.rstrip("/").split("/")[-1]
        item = await self._wait_for_item(page, slug)
        if item is None:
            raise LayoutChangedError(
                f"Новое объявление {slug} пока недоступно на Playerok"
            )
        if item.status and item.status.upper() != "DRAFT":
            return True

        entry = await self._find_visible(page, "boost.entry_buttons", timeout_ms=15000)
        if entry is None:
            raise LayoutChangedError(
                "Кнопка публикации не найдена. Проверьте boost.entry_buttons "
                "в selectors.yaml."
            )
        await self._before_action()
        await self._safe_click(entry)
        modal = await self._wait_for_modal(page)
        if modal is None:
            raise LayoutChangedError(
                "Окно продвижения не открылось, объявление осталось черновиком"
            )

        # The id of the free ("Обычный") priority is read from the modal.
        priority_id = await self._free_priority_id(page)
        await self._close_dialog(page, modal)
        if not priority_id or not item.id:
            # Fallback: publish through the modal button.
            return await self._publish_listing_via_ui(page, lot_url)

        data: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                data = await self._graphql(
                    page,
                    PUBLISH_ITEM_QUERY,
                    {
                        "input": {
                            "transactionProviderId": "LOCAL",
                            "priorityStatuses": [priority_id],
                            "itemId": item.id,
                            "keepInSale": False,
                        }
                    },
                    operation_name="publishItem",
                )
                break
            except PlayerokError as exc:
                logger.warning("publishItem failed (%s), retrying", exc)
                await page.wait_for_timeout(10000)
        if data is None:
            # Fallback: publish through the modal button.
            return await self._publish_listing_via_ui(page, lot_url)
        published = data.get("publishItem")
        if isinstance(published, dict):
            status = str(published.get("status") or "").upper()
            if status and status != "DRAFT":
                return True

        after = await self._wait_for_item(page, slug, timeout_ms=30000)
        if after is None:
            return False
        return bool(after.status and after.status.upper() != "DRAFT")

    async def _publish_listing_via_ui(self, page: Page, lot_url: str) -> bool:
        """Publish through the modal button ("Выставить бесплатно")."""
        await self._before_action()
        await self._goto(page, lot_url)
        await self._dismiss_cookie_banner(page)
        entry = await self._find_visible(page, "boost.entry_buttons", timeout_ms=15000)
        if entry is None:
            raise LayoutChangedError("Кнопка публикации не найдена")
        await self._before_action()
        await self._safe_click(entry)
        modal = await self._wait_for_modal(page)
        if modal is None:
            raise LayoutChangedError(
                "Окно продвижения не открылось, объявление осталось черновиком"
            )
        await self._select_free_option(page)
        await page.wait_for_timeout(1500)
        modal = await self._modal_locator(page) or modal
        button = await self._find_visible(
            modal, "listing.free_publish_buttons", timeout_ms=20000
        )
        if button is None:
            button = await self._find_visible(
                modal, "listing.keep_in_sale", timeout_ms=4000
            )
        if button is None:
            await self._close_dialog(page, modal)
            raise LayoutChangedError(
                "Кнопка бесплатной публикации не найдена. Проверьте "
                "listing.free_publish_buttons в selectors.yaml."
            )
        await self._before_action()
        await self._safe_click(button)
        await page.wait_for_timeout(4000)

        slug = lot_url.rstrip("/").split("/")[-1]
        if slug:
            try:
                item = await self.fetch_item(page, slug)
                if item.status and item.status.upper() != "DRAFT":
                    return True
            except PlayerokError:
                pass
        body = (await self._body_text(page)).lower()
        return "черновик" not in body

    async def _free_priority_id(self, page: Page) -> str | None:
        """Read the id of the free ("Обычный") priority from the modal."""
        markers = self.selectors.strings("listing.free_option_labels") or [
            "Обычный",
            "Бесплатно",
        ]
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            try:
                value = await self._evaluate_with_retry(
                    page,
                    """
                    (args) => {
                      const radios = Array.from(
                        document.querySelectorAll('input[name="itemPriority"]')
                      );
                      const free = radios.find((el) => {
                        const label = el.closest('label');
                        const text = label ? (label.innerText || '').trim() : '';
                        return args.markers.some(
                          (marker) => text.startsWith(marker)
                        );
                      });
                      if (!free) return '';
                      const value = free.getAttribute('value') || '';
                      if (!free.checked) {
                        free.click();
                      }
                      return value;
                    }
                    """,
                    {"markers": list(markers)},
                )
            except Exception:  # noqa: BLE001
                value = ""
            if value:
                return str(value)
            await page.wait_for_timeout(1000)
        return None

    async def _wait_for_modal(
        self, page: Page, timeout_ms: int = 20000
    ) -> Locator | None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            modal = await self._modal_locator(page)
            if modal is not None:
                try:
                    text = await self._inner_text(modal)
                except Exception:  # noqa: BLE001
                    text = ""
                if text.strip():
                    return modal
            await page.wait_for_timeout(500)
        return None

    async def _modal_locator(self, page: Page) -> Locator | None:
        modals = page.locator(".MuiModal-root")
        try:
            count = await modals.count()
        except Exception:  # noqa: BLE001
            return None
        for index in range(count - 1, -1, -1):
            node = modals.nth(index)
            try:
                if await node.is_visible():
                    return node
            except Exception:  # noqa: BLE001
                continue
        return None

    async def _select_free_option(self, page: Page) -> bool:
        """Select the free ("Обычный") priority in the promotion modal."""
        markers = self.selectors.strings("listing.free_option_labels") or [
            "Обычный",
            "Бесплатно",
        ]
        try:
            state = await self._evaluate_with_retry(
                page,
                """
                (args) => {
                  const labels = Array.from(document.querySelectorAll('label'));
                  const freeLabel = labels.find((node) => {
                    const text = (node.innerText || '').trim();
                    return args.markers.some((marker) => text.startsWith(marker));
                  });
                  if (!freeLabel) return {selected: false};
                  const radio = freeLabel.querySelector(
                    'input[name="itemPriority"]'
                  );
                  if (!radio) return {selected: false};
                  if (!radio.checked) {
                    freeLabel.click();
                    return {selected: false, clicked: true};
                  }
                  return {selected: true, clicked: false};
                }
                """,
                {"markers": list(markers)},
            )
        except Exception:  # noqa: BLE001
            return False
        if state.get("clicked"):
            await page.wait_for_timeout(1200)
            try:
                state = await self._evaluate_with_retry(
                    page,
                    """
                    (args) => {
                      const labels = Array.from(
                        document.querySelectorAll('label')
                      );
                      const freeLabel = labels.find((node) => {
                        const text = (node.innerText || '').trim();
                        return args.markers.some(
                          (marker) => text.startsWith(marker)
                        );
                      });
                      const radio = freeLabel
                        ? freeLabel.querySelector('input[name="itemPriority"]')
                        : null;
                      return {selected: Boolean(radio && radio.checked)};
                    }
                    """,
                    {"markers": list(markers)},
                )
            except Exception:  # noqa: BLE001
                return False
        return bool(state.get("selected"))

    async def _ensure_premium_option(self, page: Page) -> bool:
        """Make sure the "Премиум" priority is selected in the promotion modal."""
        try:
            state = await self._evaluate_with_retry(
                page,
                """
                () => {
                  const labels = Array.from(document.querySelectorAll('label'));
                  const premiumLabel = labels.find((node) =>
                    (node.innerText || '').trim().startsWith('Премиум')
                  );
                  if (!premiumLabel) return {selected: false, clicked: false};
                  const radio = premiumLabel.querySelector(
                    'input[name="itemPriority"]'
                  );
                  if (!radio) return {selected: false, clicked: false};
                  if (!radio.checked) {
                    premiumLabel.click();
                    return {selected: false, clicked: true};
                  }
                  return {selected: true, clicked: false};
                }
                """
            )
        except Exception:  # noqa: BLE001
            return False
        if state.get("clicked"):
            await page.wait_for_timeout(900)
            try:
                state = await self._evaluate_with_retry(
                    page,
                    """
                    () => {
                      const labels = Array.from(
                        document.querySelectorAll('label')
                      );
                      const premiumLabel = labels.find((node) =>
                        (node.innerText || '').trim().startsWith('Премиум')
                      );
                      const radio = premiumLabel
                        ? premiumLabel.querySelector('input[name="itemPriority"]')
                        : null;
                      return {selected: Boolean(radio && radio.checked)};
                    }
                    """
                )
            except Exception:  # noqa: BLE001
                return False
        return bool(state.get("selected"))

    async def _payment_dialog(self, page: Page) -> Locator | None:
        """Return the payment window (the topmost modal)."""
        deadline = asyncio.get_running_loop().time() + 6
        while asyncio.get_running_loop().time() < deadline:
            candidates: list[Locator] = []
            for spec in self.selectors.specs("payment.dialog"):
                locator = self._locator(page, spec)
                if locator is None:
                    continue
                try:
                    count = await locator.count()
                except Exception:  # noqa: BLE001
                    continue
                for index in range(count):
                    node = locator.nth(index)
                    try:
                        if await node.is_visible():
                            candidates.append(node)
                    except Exception:  # noqa: BLE001
                        continue
            for node in reversed(candidates):
                try:
                    text = (await self._inner_text(node)).lower()
                except Exception:  # noqa: BLE001
                    continue
                if "оплатить с баланса" in text or "перейти к оплате" in text:
                    return node
            if candidates:
                return candidates[-1]
            await page.wait_for_timeout(300)
        return None

    async def _inspect_payment(self, dialog: Locator) -> PaymentInspection:
        """Detect the payment source inside the Playerok payment window.

        Playerok shows the main balance as a switch with ``providerId=LOCAL``.
        The bot only pays when that switch is enabled; card and crypto options
        are never used.
        """
        dialog_text = await self._inner_text(dialog)
        lowered = dialog_text.lower()
        frozen_markers = self.selectors.strings("payment.frozen_markers")
        other_markers = self.selectors.strings("payment.other_source_markers")

        balance_checked, balance_text = await self._balance_switch_state(dialog)
        card_checked, card_text = await self._card_source_state(dialog)

        source = "unknown"
        source_text = ""
        if balance_checked:
            source = "main"
            source_text = balance_text or "Оплатить с баланса (основной баланс)"
        elif card_checked:
            source = "other"
            source_text = card_text or "Оплата картой или СБП"
        elif any(marker.lower() in lowered for marker in frozen_markers):
            source = "frozen"
            source_text = "Средства в заморозке"

        if source == "unknown":
            # Fail-safe: only infer the main balance when the window shows the
            # balance and no frozen/other source at all.
            has_frozen = any(marker.lower() in lowered for marker in frozen_markers)
            has_other = any(marker.lower() in lowered for marker in other_markers)
            has_main = "с баланса" in lowered or "баланс" in lowered
            if has_main and not has_frozen and not has_other:
                source = "main"
                source_text = "Основной баланс (единственный источник в окне)"

        amount = await self._payment_amount(dialog)
        return PaymentInspection(
            amount=amount,
            source=source,
            source_text=truncate(source_text, 300),
            dialog_text=truncate(dialog_text, 2000),
        )

    async def _balance_switch_state(self, dialog: Locator) -> tuple[bool, str]:
        for spec in self.selectors.specs("payment.balance_switch"):
            locator = self._locator(dialog, spec)
            if locator is None:
                continue
            try:
                count = await locator.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 3)):
                node = locator.nth(index)
                try:
                    if not await node.is_visible():
                        continue
                    if not await node.is_checked():
                        continue
                except Exception:  # noqa: BLE001
                    continue
                label = node.locator("xpath=ancestor::label[1]")
                text = ""
                try:
                    if await label.count():
                        text = await self._inner_text(label.first)
                except Exception:  # noqa: BLE001
                    text = ""
                return True, text.strip()
        return False, ""

    async def _card_source_state(self, dialog: Locator) -> tuple[bool, str]:
        for spec in self.selectors.specs("payment.card_radios"):
            locator = self._locator(dialog, spec)
            if locator is None:
                continue
            try:
                count = await locator.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 10)):
                node = locator.nth(index)
                try:
                    if not await node.is_visible():
                        continue
                    if not await node.is_checked():
                        continue
                except Exception:  # noqa: BLE001
                    continue
                label = node.locator("xpath=ancestor::label[1]")
                text = ""
                try:
                    if await label.count():
                        text = await self._inner_text(label.first)
                except Exception:  # noqa: BLE001
                    text = ""
                return True, text.strip()
        return False, ""

    async def _balance_amount(self, dialog: Locator) -> Decimal | None:
        for spec in self.selectors.specs("payment.balance_labels"):
            locator = self._locator(dialog, spec)
            if locator is None:
                continue
            try:
                count = await locator.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 3)):
                node = locator.nth(index)
                try:
                    if not await node.is_visible():
                        continue
                    text = await self._inner_text(node)
                except Exception:  # noqa: BLE001
                    continue
                amount = parse_money(text)
                if amount is not None:
                    return amount
        return None

    async def _payment_amount(self, dialog: Locator) -> Decimal | None:
        """Return the amount to pay, ignoring the balance amount."""
        try:
            text = await self._inner_text(dialog)
        except Exception:  # noqa: BLE001
            return None
        balance = await self._balance_amount(dialog)
        amounts: list[Decimal] = []
        for match in re.findall(r"([\d][\d\s\u00a0.,]*)\s*₽", text):
            amount = parse_money(match)
            if amount is None:
                continue
            if balance is not None and amount == balance:
                continue
            amounts.append(amount)
        if not amounts:
            return None
        return min(amounts)

    @staticmethod
    def _parse_premium_price(text: str) -> Decimal | None:
        """Parse "Повысить до Премиум за 99 ₽ 149" -> 99."""
        match = re.search(r"за\s*([\d][\d\s\u00a0.,]*)\s*₽", text or "")
        if match:
            return parse_money(match.group(1))
        amounts = [
            parse_money(item)
            for item in re.findall(r"([\d][\d\s\u00a0.,]*)\s*₽", text or "")
        ]
        amounts = [item for item in amounts if item is not None]
        return min(amounts) if amounts else None

    async def _close_dialog(self, page: Page, dialog: Locator | None) -> None:
        button = None
        if dialog is not None:
            button = await self._find_visible(
                dialog, "payment.cancel_buttons", timeout_ms=1000
            )
        if button is None:
            button = await self._find_visible(
                page, "payment.cancel_buttons", timeout_ms=800
            )
        if button is not None:
            try:
                await button.click(timeout=2000)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            await page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass

    async def _dialog_gone(self, page: Page, dialog: Locator) -> bool:
        try:
            return not await dialog.is_visible()
        except Exception:  # noqa: BLE001
            return True

    async def _wait_for_texts(
        self, page: Page, path: str, *, timeout_ms: int
    ) -> bool:
        markers = [marker.lower() for marker in self.selectors.strings(path)]
        if not markers:
            return False
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            body = (await self._body_text(page)).lower()
            if any(marker in body for marker in markers):
                return True
            await page.wait_for_timeout(300)
        return False

    async def _extract_lot_url(self, page: Page) -> str | None:
        if "/products/" in page.url:
            return page.url.split("#", 1)[0]
        link = await self._find_visible(page, "relist.item_links", timeout_ms=800)
        if link is not None:
            href = await link.get_attribute("href")
            if href:
                return self._absolute(href)
        return None

    async def _username_from_page(self, page: Page) -> str | None:
        excluded = {
            str(item).lower()
            for item in self.selectors.strings("session.exclude_username_segments")
        }
        match = re.search(r"/profile/([^/?#]+)", page.url)
        if match and match.group(1).lower() not in excluded:
            return match.group(1)
        try:
            links = await self._evaluate_with_retry(
                page,
                """
                () => Array.from(document.querySelectorAll('a[href^="/profile/"]'))
                  .map((a) => a.getAttribute('href'))
                  .filter(Boolean)
                """
            )
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(links, list):
            return None
        for href in links:
            match = re.search(r"/profile/([^/?#]+)", str(href))
            if not match:
                continue
            candidate = match.group(1)
            if candidate.lower() not in excluded:
                return candidate
        return None

    async def _goto(self, page: Page, url: str) -> Response | None:
        response: Response | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            if self._stop_check():
                raise StopRequestedError("Остановка запрошена")
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.config.navigation_timeout_ms,
                )
                break
            except PlaywrightTimeoutError as exc:
                # A slow page is not a layout change yet: retry once before
                # stopping all tasks.
                if attempt == 0:
                    logger.warning("Navigation timeout, retrying: %s", url)
                    await page.wait_for_timeout(3000)
                    continue
                raise LayoutChangedError(
                    f"Таймаут загрузки страницы: {truncate(url, 200)}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not self._is_network_error(exc):
                    raise
                logger.warning("Navigation failed (%s), retrying", exc)
                await page.wait_for_timeout(3000)
        else:
            raise PlayerokError(
                f"Не удалось открыть страницу: {truncate(str(last_error), 200)}"
            )
        if response is not None and response.status == 429:
            raise RateLimitError("Playerok вернул 429")
        await self._check_page_errors(page)
        response = (await self._wait_for_challenge(page)) or response
        # A freshly created listing may answer "not found" for a short while.
        for _ in range(5):
            body = (await self._body_text(page)).lower()
            if "страница не найдена" not in body:
                break
            logger.info("Listing page is not ready yet, reloading")
            await page.wait_for_timeout(8000)
            try:
                response = await page.reload(
                    wait_until="domcontentloaded",
                    timeout=self.config.navigation_timeout_ms,
                )
            except Exception:  # noqa: BLE001
                break
        await self._wait_for_content(page)
        return response

    def _is_challenge(self, body: str) -> bool:
        markers = [
            marker.lower()
            for marker in self.selectors.strings("errors.challenge_texts")
        ]
        lowered = (body or "").lower()
        return any(marker in lowered for marker in markers)

    async def _wait_for_challenge(self, page: Page) -> Response | None:
        """Wait out the Playerok "browser verification" page.

        Playerok shows this page for an IP it has not seen before. The page
        verifies the browser by itself and reloads automatically, so the bot
        only waits and watches the page - reloading would interrupt the check.
        """
        markers = [
            marker.lower()
            for marker in self.selectors.strings("errors.challenge_texts")
        ]
        if not markers:
            return None
        wait_seconds = max(5, self.config.challenge_wait_seconds)
        for attempt in range(self.config.challenge_max_attempts):
            try:
                body = (await self._body_text(page)).lower()
            except Exception:  # noqa: BLE001
                body = ""
            if not any(marker in body for marker in markers):
                logger.info("Playerok browser check passed")
                return None
            logger.info(
                "Playerok browser challenge detected (attempt %s), waiting %ss",
                attempt + 1,
                wait_seconds,
            )
            await page.wait_for_timeout(wait_seconds * 1000)
            try:
                body = (await self._body_text(page)).lower()
            except Exception:  # noqa: BLE001
                body = ""
            if not any(marker in body for marker in markers):
                logger.info("Playerok browser check passed")
                return None
            if attempt + 1 == self.config.challenge_max_attempts:
                # Last resort: a manual reload may pick up the verified state.
                try:
                    await page.reload(
                        wait_until="domcontentloaded",
                        timeout=self.config.navigation_timeout_ms,
                    )
                except Exception:  # noqa: BLE001
                    pass
        logger.warning("Playerok browser challenge did not pass")
        return None

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        text = str(exc)
        return any(
            marker in text
            for marker in (
                "ERR_NETWORK_CHANGED",
                "ERR_CONNECTION_RESET",
                "ERR_CONNECTION_CLOSED",
                "ERR_INTERNET_DISCONNECTED",
                "ERR_NAME_NOT_RESOLVED",
                "ERR_EMPTY_RESPONSE",
                "ERR_TIMED_OUT",
            )
        )

    async def _wait_for_content(
        self, page: Page, timeout_ms: int = 15000
    ) -> None:
        """Wait until the SPA has rendered its content.

        Playerok renders listings client-side, so the DOM can still be empty
        right after the navigation.
        """
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            try:
                length = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').length"
                )
            except Exception:  # noqa: BLE001
                await page.wait_for_timeout(300)
                continue
            if isinstance(length, int) and length > 300:
                return
            await page.wait_for_timeout(300)

    async def _check_page_errors(self, page: Page) -> None:
        for spec in self.selectors.specs("errors.captcha_selectors"):
            locator = self._locator(page, spec)
            try:
                if locator is not None and await locator.count():
                    if await locator.first.is_visible():
                        raise CaptchaError("Обнаружена капча")
            except CaptchaError:
                raise
            except Exception:  # noqa: BLE001
                continue
        body = (await self._body_text(page)).lower()
        captcha_texts = [
            text.lower()
            for text in self.selectors.strings("errors.captcha_texts")
            if len(text) > 3
        ]
        if any(text in body for text in captcha_texts):
            raise CaptchaError("Обнаружена капча")
        rate_texts = [
            text.lower()
            for text in self.selectors.strings("errors.rate_limit_texts")
            if len(text) > 4 and text != "429"
        ]
        if any(text in body for text in rate_texts):
            raise RateLimitError("Playerok ограничил запросы")

    async def _before_action(self) -> None:
        if self._stop_check():
            raise StopRequestedError("Остановка запрошена")
        delay = random.uniform(
            self.config.action_delay_min_seconds,
            self.config.action_delay_max_seconds,
        )
        logger.debug("Pacing Playerok action: %.1f s", delay)
        await asyncio.sleep(delay)
        if self._stop_check():
            raise StopRequestedError("Остановка запрошена")

    async def _safe_click(self, locator: Locator) -> None:
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.click(timeout=8000)
        except PlaywrightTimeoutError as exc:
            raise LayoutChangedError("Элемент найден, но не удалось кликнуть") from exc

    async def _find_visible(
        self,
        root: Page | Locator,
        path: str,
        *,
        timeout_ms: int = 2500,
    ) -> Locator | None:
        specs = self.selectors.specs(path)
        if not specs:
            return None
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while True:
            for spec in specs:
                locator = self._locator(root, spec)
                if locator is None:
                    continue
                try:
                    count = await locator.count()
                except Exception:  # noqa: BLE001
                    continue
                for index in range(min(count, 5)):
                    candidate = locator.nth(index)
                    try:
                        if await candidate.is_visible():
                            return candidate
                    except Exception:  # noqa: BLE001
                        continue
            if asyncio.get_running_loop().time() >= deadline:
                return None
            page = root if isinstance(root, Page) else root.page
            await page.wait_for_timeout(200)

    async def _find_all(
        self, root: Page | Locator, path: str
    ) -> Locator | None:
        for spec in self.selectors.specs(path):
            locator = self._locator(root, spec)
            if locator is None:
                continue
            try:
                if await locator.count():
                    return locator
            except Exception:  # noqa: BLE001
                continue
        return None

    def _locator(
        self, root: Page | Locator, spec: dict[str, Any]
    ) -> Locator | None:
        try:
            if spec.get("css"):
                return root.locator(str(spec["css"]))
            if spec.get("xpath"):
                return root.locator("xpath=" + str(spec["xpath"]))
            if spec.get("text"):
                return root.get_by_text(str(spec["text"]), exact=bool(spec.get("exact", False)))
            if spec.get("role"):
                role = str(spec["role"])
                name = spec.get("name")
                if name is not None:
                    return root.get_by_role(
                        role, name=str(name), exact=bool(spec.get("exact", False))
                    )
                return root.get_by_role(role)
            if spec.get("placeholder"):
                return root.get_by_placeholder(str(spec["placeholder"]))
            if spec.get("label"):
                return root.get_by_label(str(spec["label"]))
            if spec.get("test_id"):
                return root.get_by_test_id(str(spec["test_id"]))
        except Exception:  # noqa: BLE001
            return None
        return None

    async def _relative_text(self, root: Locator, path: str) -> str:
        locator = await self._find_visible(root, path, timeout_ms=300)
        if locator is None:
            return ""
        return await self._inner_text(locator)

    @staticmethod
    async def _inner_text(locator: Locator) -> str:
        try:
            return (await locator.inner_text(timeout=2000)).strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    async def _body_text(page: Page) -> str:
        try:
            return await page.locator("body").inner_text(timeout=3000)
        except Exception:  # noqa: BLE001
            return ""

    def _absolute(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.config.playerok_base_url}/{path.lstrip('/')}"

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None


__all__ = [
    "BoostOutcome",
    "ChatInfo",
    "ItemInfo",
    "ListingResult",
    "PaymentInspection",
    "PlayerokClient",
    "SaleInfo",
    "SessionInfo",
]
