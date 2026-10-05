"""Собирает очередь бойцов по умолчанию для поставки.

Зачем это нужно. Файл latest_brawler_data.json не попадает в репозиторий
(.gitignore), и в репозитории он весит два байта - пустой список. Установленная
программа брала его с собой и показывала на старте ноль бойцов: играть было
нечем, пока бот не наткнулся бы на кого-то сам.

Скрипт идёт от иконок: имена файлов в api/assets/brawler_icons и
brawler_icons2 - это и есть перечень бойцов, который панель и так показывает
в списке доступных. Один и тот же боец может лежать в обеих папках, поэтому
имена объединяются, а к списку добавляются те, у кого иконки нет (jess) -
иначе он не был бы виден в панели.

    python -B tools_make_default_queue.py <куда положить.json>
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent

ICON_DIRS = ("api/assets/brawler_icons", "api/assets/brawler_icons2")

# Бойцы без иконки. Список рядом с иконками держать нельзя: иконки - это
# единственное, что попадает в сборку, а забытый здесь боец просто выпадает
# из панели молча.
EXTRA_BRAWLERS = ("jess",)


def collect() -> list[str]:
    names: set[str] = set()
    for folder in ICON_DIRS:
        for item in (ROOT / folder).glob("*"):
            if item.is_file():
                names.add(item.stem.lower())
    names.update(EXTRA_BRAWLERS)
    return sorted(names)


def build() -> list[dict]:
    # Порядок не важен: бот всё равно сортирует бойцов по своим правилам, а
    # очередь по трофеям он пересобирает сам. Значения - нули, чтобы панель
    # не показывала чужие цифры.
    return [
        {
            "brawler": name,
            "type": "trophies",
            "trophies": 0,
            "wins": 0,
            "push_until": 1000,
            "automatically_pick": True,
            "win_streak": 0,
        }
        for name in collect()
    ]


def main() -> int:
    queue = build()
    if not queue:
        print("Ни одного бойца не нашлось - проверьте папки с иконками.", flush=True)
        return 1

    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "latest_brawler_data.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Записано {len(queue)} бойцов в {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())