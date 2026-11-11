"""Check the status of the old and the new bot token."""

from __future__ import annotations

import json
import urllib.request

OLD = "8945077406:AAEK0wmPIXow-gc-PiGDCdOVRhphFSMWHDU"
NEW = "8945077406:AAF5HECHKjwBRv1ycoJCwelAV8HL9Lq20mw"


def check(label: str, token: str) -> None:
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=20
        ) as response:
            data = json.loads(response.read().decode())
        print(label, "->", "ЖИВОЙ" if data.get("ok") else data.get("description"))
    except Exception as exc:  # noqa: BLE001
        print(label, "-> ОТОЗВАН/НЕВАЛИДЕН:", exc)


def main() -> None:
    check("старый токен", OLD)
    check("новый токен ", NEW)


if __name__ == "__main__":
    main()
