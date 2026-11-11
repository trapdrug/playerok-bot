"""Проверка cookies Playerok без запуска бота.

Загружает cookies.json, открывает /profile в headless-браузере и сообщает,
авторизована ли сессия. Значения cookies не печатает.

Запуск:
    python scripts/check_cookies.py
    python scripts/check_cookies.py --file cookies.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


DEFAULT_FILE = Path("cookies.json")
PROFILE_URL = "https://playerok.com/profile"
LOGIN_MARKERS = ("вход или регистрация", "получить код", "войти")
LOGIN_URL_MARKERS = ("/profile/auth", "/auth")


def _load_cookies(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        # Playwright storage_state format.
        data = data.get("cookies", [])
    if not isinstance(data, list):
        raise ValueError("Ожидался JSON-массив cookies")
    cookies: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        cookies.append(
            {
                "name": str(name),
                "value": str(item.get("value") or ""),
                "domain": str(item.get("domain") or ".playerok.com"),
                "path": str(item.get("path") or "/"),
                "secure": bool(item.get("secure", True)),
                "httpOnly": bool(item.get("httpOnly", False)),
            }
        )
    return cookies


async def _check(path: Path) -> int:
    cookies = _load_cookies(path)
    print(f"Файл: {path.resolve()}")
    print(f"Cookies: {len(cookies)}")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox"]
        )
        context = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        try:
            response = await page.goto(
                PROFILE_URL, wait_until="domcontentloaded", timeout=60000
            )
            await page.wait_for_timeout(4000)

            body = await page.locator("body").inner_text(timeout=5000)
            lowered = body.lower()
            url = page.url
            title = await page.title()

            login_inputs = await page.locator(
                'input[type="email"], input[autocomplete="email"], '
                'input[name="email"]'
            ).count()
            has_login_url = any(marker in url for marker in LOGIN_URL_MARKERS)
            has_login_text = any(marker in lowered for marker in LOGIN_MARKERS)
            has_logout = "выйти" in lowered or "выход" in lowered

            print(f"HTTP: {response.status if response else None}")
            print(f"URL: {url}")
            print(f"TITLE: {title}")
            print(f"login_inputs: {login_inputs}")
            print(f"has_login_url: {has_login_url}")
            print(f"has_login_text: {has_login_text}")
            print(f"has_logout: {has_logout}")

            authenticated = (
                not has_login_url
                and not has_login_text
                and login_inputs == 0
            )
            if authenticated:
                print("РЕЗУЛЬТАТ: сессия выглядит авторизованной")
                return 0

            print("РЕЗУЛЬТАТ: сессия НЕ авторизована (видна форма входа)")
            return 2
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка cookies Playerok")
    parser.add_argument(
        "--file",
        default=str(DEFAULT_FILE),
        help="Путь к cookies.json (по умолчанию cookies.json)",
    )
    args = parser.parse_args()

    path = Path(args.file).expanduser()
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        raise SystemExit(1)

    raise SystemExit(asyncio.run(_check(path)))


if __name__ == "__main__":
    main()
