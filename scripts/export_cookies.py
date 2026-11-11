"""Экспорт cookies Playerok в формате, который принимает бот.

Скрипт открывает настоящий браузер Chromium с постоянным профилем.
Вы входите в свой аккаунт Playerok вручную, после чего скрипт сам
сохраняет cookies в JSON-файл. Этот файл потом отправляется боту.

Запуск:
    python scripts/export_cookies.py
    python scripts/export_cookies.py --auto --timeout 900
    python scripts/export_cookies.py --output data/cookies.json

Требуется установленный Chromium:
    python -m playwright install chromium

ВАЖНО: получившийся файл содержит действующую сессию. Не отправляйте его
никому, кроме своего бота, и не коммитьте в git.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


ALLOWED_DOMAIN = "playerok.com"
DEFAULT_URL = "https://playerok.com/profile"
DEFAULT_OUTPUT = Path("cookies.json")
DEFAULT_PROFILE_DIR = Path("data") / "export-profile"

LOGIN_URL_MARKERS = ("/profile/auth", "/auth")
LOGIN_TEXT_MARKERS = (
    "вход или регистрация",
    "получить код",
    "войти в аккаунт",
)
PROFILE_MARKERS = (
    "выйти",
    "мои лоты",
    "мои товары",
    "мои продажи",
    "мой профиль",
    "кошелёк",
    "баланс",
    "покупки",
    "продажи",
    "активные",
    "завершённые",
)


def _domain_allowed(domain: str) -> bool:
    normalised = domain.lstrip(".").lower()
    return normalised == ALLOWED_DOMAIN or normalised.endswith(
        "." + ALLOWED_DOMAIN
    )


def _normalise(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Привести cookies Playwright к формату, который ждёт бот."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if not _domain_allowed(domain):
            continue

        name = str(cookie.get("name") or "").strip()
        if not name:
            continue
        value = str(cookie.get("value") or "")
        path = str(cookie.get("path") or "/") or "/"

        key = (name, domain, path)
        if key in seen:
            continue
        seen.add(key)

        item: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": bool(cookie.get("secure", True)),
            "httpOnly": bool(cookie.get("httpOnly", False)),
        }

        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            item["expires"] = float(expires)

        same_site = cookie.get("sameSite")
        if same_site in ("Strict", "Lax", "None"):
            item["sameSite"] = same_site

        result.append(item)

    return result


async def _looks_logged_in(page) -> bool:  # noqa: ANN001
    url = page.url
    if any(marker in url for marker in LOGIN_URL_MARKERS):
        return False

    try:
        cookies = await page.context.cookies()
    except Exception:  # noqa: BLE001
        return False
    if not any(
        _domain_allowed(str(cookie.get("domain") or ""))
        for cookie in cookies
    ):
        return False

    try:
        body = await page.locator("body").inner_text(timeout=3000)
    except Exception:  # noqa: BLE001
        return False

    lowered = body.lower()
    if any(marker in lowered for marker in LOGIN_TEXT_MARKERS):
        return False

    try:
        login_inputs = await page.locator(
            'input[type="email"], input[autocomplete="email"], '
            'input[name="email"]'
        ).count()
    except Exception:  # noqa: BLE001
        login_inputs = 0
    if login_inputs:
        return False

    # Требуем минимум два признака личного кабинета, чтобы не принять
    # публичную страницу за авторизованную.
    matches = sum(marker in lowered for marker in PROFILE_MARKERS)
    return matches >= 2


async def _save(
    context, output: Path  # noqa: ANN001
) -> int:
    cookies = await context.cookies()
    normalised = _normalise(cookies)
    if not normalised:
        print(
            "Не найдено ни одной cookie для playerok.com. "
            "Проверьте, что вы вошли в аккаунт.",
            file=sys.stderr,
        )
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(normalised, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Сохранено cookies: {len(normalised)}", flush=True)
    print(f"Файл: {output}", flush=True)
    print(
        "Отправьте этот файл боту в Telegram (просто документом). "
        "Сообщение будет удалено, cookies — зашифрованы.",
        flush=True,
    )
    return 0


async def _run(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=args.headless,
                locale="ru-RU",
                timezone_id=args.timezone,
                viewport={"width": 1366, "height": 900},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Не удалось запустить Chromium: {exc}", file=sys.stderr)
            print(
                "Установите браузер: python -m playwright install chromium",
                file=sys.stderr,
            )
            return 1

        try:
            page = (
                context.pages[0]
                if context.pages
                else await context.new_page()
            )

            try:
                await page.goto(args.url, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Не удалось открыть {args.url}: {exc}", file=sys.stderr
                )
                return 1

            print("=" * 70, flush=True)
            print(
                "Открыт браузер. Войдите в аккаунт Playerok и откройте "
                "свой профиль.",
                flush=True,
            )
            print(f"Профиль браузера: {profile_dir}", flush=True)
            print("=" * 70, flush=True)

            if args.auto:
                print(
                    "Ожидаю вход. После входа cookies сохранятся "
                    "автоматически.",
                    flush=True,
                )
                print(
                    f"Таймаут ожидания: {args.timeout} сек. Ctrl+C — отмена.",
                    flush=True,
                )
                deadline = asyncio.get_running_loop().time() + args.timeout
                confirmed = 0
                while confirmed < 2:
                    if asyncio.get_running_loop().time() >= deadline:
                        print("Таймаут: вход не выполнен.", file=sys.stderr)
                        return 1
                    if await _looks_logged_in(page):
                        confirmed += 1
                        print(
                            f"Похоже на вход ({confirmed}/2), "
                            f"URL: {page.url}",
                            flush=True,
                        )
                    else:
                        confirmed = 0
                    await asyncio.sleep(2)
                await asyncio.sleep(2)
            else:
                while True:
                    answer = await asyncio.to_thread(
                        input,
                        "Enter — сохранить cookies, q — отмена: ",
                    )
                    if answer.strip().lower() in ("q", "quit", "exit", "й"):
                        print("Отменено.")
                        return 1
                    if not await _looks_logged_in(page):
                        print(
                            "Похоже, вы ещё не вошли в аккаунт или открыта "
                            "не страница профиля. Войдите и нажмите Enter "
                            "ещё раз."
                        )
                        continue
                    break

            return await _save(context, output)
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Экспорт cookies Playerok для Telegram-бота"
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Куда сохранить JSON (по умолчанию cookies.json)",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Стартовый URL (по умолчанию https://playerok.com/profile)",
    )
    parser.add_argument(
        "--timezone",
        default="Europe/Moscow",
        help="Часовой пояс браузера",
    )
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help="Каталог постоянного профиля браузера",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Запустить без окна (не подходит для ручного входа)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Ждать вход автоматически и сохранить cookies без Enter",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Таймаут ожидания входа в авто-режиме, секунд (по умолчанию 600)",
    )
    args = parser.parse_args()

    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
