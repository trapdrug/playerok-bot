"""Update the working bot token in the local .env file."""

from __future__ import annotations

from pathlib import Path


TOKEN = "8945077406:AAEK0wmPIXow-gc-PiGDCdOVRhphFSMWHDU"

path = Path(".env")
text = path.read_text(encoding="utf-8")
lines = text.splitlines()
updated = []
replaced = False
for line in lines:
    if line.strip().startswith("BOT_TOKEN="):
        updated.append(f"BOT_TOKEN={TOKEN}")
        replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append(f"BOT_TOKEN={TOKEN}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
print("BOT_TOKEN обновлён в .env:", replaced)
