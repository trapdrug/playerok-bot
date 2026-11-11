from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class SelectorsError(Exception):
    pass


class Selectors:
    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            raise SelectorsError(f"Файл селекторов не найден: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise SelectorsError("selectors.yaml должен содержать YAML-объект")
        self._data: dict[str, Any] = data

    def get(self, dotted_path: str, default: Any = None) -> Any:
        value: Any = self._data
        for part in dotted_path.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def specs(self, dotted_path: str) -> list[dict[str, Any]]:
        value = self.get(dotted_path, [])
        if not isinstance(value, list):
            return []
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                result.append({"css": item})
            elif isinstance(item, dict):
                result.append(item)
        return result

    def strings(self, dotted_path: str) -> list[str]:
        value = self.get(dotted_path, [])
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if isinstance(item, (str, int, float))]

    def url(self, name: str, **kwargs: Any) -> str:
        path = self.get(f"urls.{name}", "")
        if not isinstance(path, str) or not path:
            raise SelectorsError(f"URL '{name}' не задан в selectors.yaml")
        try:
            return path.format(**kwargs)
        except KeyError as exc:
            raise SelectorsError(f"Не хватает параметра {exc} для URL '{name}'") from exc


__all__ = ["Selectors", "SelectorsError"]
