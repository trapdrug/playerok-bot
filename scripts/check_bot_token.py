"""Check which bot token is valid via the Telegram API."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check(label: str, token: str) -> None:
    if not token:
        print(label, "-> пусто")
        return
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.loads(response.read().decode())
        if data.get("ok"):
            print(label, "-> OK, бот:", data["result"].get("username"))
        else:
            print(label, "-> отказ:", data.get("description"))
    except Exception as exc:  # noqa: BLE001
        print(label, "-> ошибка:", exc)


def main() -> None:
    env = read_env(Path(".env"))
    check("из .env      ", env.get("BOT_TOKEN", ""))
    check("из сообщения ", "8945077406:AAEK0wmPIXow-gc-PiGDCdOVRhphFSMWHDU")


if __name__ == "__main__":
    main()
