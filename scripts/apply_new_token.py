"""Apply a new bot token: verify it, set it on Railway and in the local .env."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

RAILWAY = r"C:\Users\Administrator\AppData\Local\Programs\nodejs\railway.cmd"
PROJECT = Path(__file__).resolve().parent.parent
TOKEN = "8945077406:AAF5HECHKjwBRv1ycoJCwelAV8HL9Lq20mw"


def check_token(token: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.loads(response.read().decode())
        if data.get("ok"):
            print("Токен валиден, бот:", data["result"].get("username"))
            return True
        print("Токен отклонён:", data.get("description"))
    except Exception as exc:  # noqa: BLE001
        print("Ошибка проверки токена:", exc)
    return False


def update_env(path: Path, token: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    replaced = False
    for line in lines:
        if line.strip().startswith("BOT_TOKEN="):
            out.append(f"BOT_TOKEN={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"BOT_TOKEN={token}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("Локальный .env обновлён:", replaced)


def set_railway_variable(name: str, value: str) -> None:
    result = subprocess.run(
        [RAILWAY, "variable", "set", f"{name}={value}"],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"Railway {name}:", (result.stdout or result.stderr).strip()[:120])


def main() -> None:
    if not check_token(TOKEN):
        print("Новый токен не работает — ничего не меняю.")
        return
    set_railway_variable("BOT_TOKEN", TOKEN)
    update_env(PROJECT / ".env", TOKEN)


if __name__ == "__main__":
    main()
