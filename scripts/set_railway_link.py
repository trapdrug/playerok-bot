"""Persist the linked Railway service in the CLI config."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path.home() / ".railway" / "config.json"
SERVICE = "6df8a48a-3428-4532-be98-97477074971b"

config = json.loads(CONFIG.read_text(encoding="utf-8"))
for path, data in config.get("projects", {}).items():
    if data.get("project") == "a3ec3a91-f148-40a3-8753-95003cdf1ed9":
        data["service"] = SERVICE
        print("service set for", path)
CONFIG.write_text(
    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("done")
