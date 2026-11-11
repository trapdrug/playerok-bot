"""Check the boost skip triggers against real listing titles."""

from __future__ import annotations

from pathlib import Path

from app.playerok.selectors import Selectors


def main() -> None:
    selectors = Selectors(Path("selectors.yaml"))
    triggers = selectors.strings("boost.skip_titles")
    print("triggers:", triggers)
    titles = [
        "ДОПЛАТА ПОДАРКА",
        "доплата подарок",
        "🐇 JELLY BUNNY - ЛЮБАЯ МОДЕЛЬ | ⚡️ БЫСТРАЯ ВЫДАЧА | 🎨 ЛЮБОЙ ФОН",
        "⭐️ SWAG BAG - ЛЮБАЯ МОДЕЛЬ | ⚡️ БЫСТРАЯ ВЫДАЧА | 🎨 ЛЮБОЙ ФОН",
        "доплата ПОДАРКА",
    ]
    for title in titles:
        lowered = title.casefold()
        matched = [
            phrase
            for phrase in triggers
            if phrase.strip().casefold() in lowered
        ]
        print(f"{'SKIP ' if matched else 'BOOST'} | {title} | {matched}")


if __name__ == "__main__":
    main()
