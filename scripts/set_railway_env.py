"""Set the Railway variables (token from the user) for the playerok-bot service."""

from __future__ import annotations

import subprocess
from pathlib import Path

RAILWAY = r"C:\Users\Administrator\AppData\Local\Programs\nodejs\railway.cmd"
PROJECT = r"C:\Users\Administrator\Documents\Проект по умолчанию"

TOKEN = "8945077406:AAEK0wmPIXow-gc-PiGDCdOVRhphFSMWHDU"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def run(args: list[str]) -> str:
    result = subprocess.run(
        [RAILWAY, *args],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (result.stdout or result.stderr).strip()


def main() -> None:
    env = read_env(Path(PROJECT) / ".env")
    from cryptography.fernet import Fernet

    fernet_key = Fernet.generate_key().decode()
    pairs = {
        "BOT_TOKEN": TOKEN,
        "ADMIN_LOGIN": env.get("ADMIN_LOGIN", "gladmin"),
        "ADMIN_INITIAL_PASSWORD": env.get("ADMIN_INITIAL_PASSWORD", ""),
        "FERNET_KEY": fernet_key,
        "DRY_RUN": "true",
        "TIMEZONE": env.get("TIMEZONE", "Europe/Moscow"),
        "POLL_INTERVAL_SECONDS": env.get("POLL_INTERVAL_SECONDS", "60"),
    }
    print("token from .env == user token:",
          env.get("BOT_TOKEN") == TOKEN)
    for key, value in pairs.items():
        if not value:
            print(key, "-> SKIPPED (empty)")
            continue
        print(key, "->", run(["variable", "set", f"{key}={value}"])[:100])


if __name__ == "__main__":
    main()
