from __future__ import annotations

import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "logs" / "logs.txt"

_echo: bool = False


def set_echo(enabled: bool) -> None:
    """Печатать каждое сообщение ещё и в консоль (по умолчанию нет: консоль остаётся чистой)."""
    global _echo
    _echo = enabled


def _stamp() -> str:
    return _dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def session_start() -> None:
    """Разделитель новой сессии в файле логов — как в курсовой: [время] Запуск системы."""
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"\n[{_stamp()}] ==== Запуск планировщика доставки ====\n")


def log(category: str, message: str, echo: bool | None = None) -> None:
    """Пишет `[время] [Категория] сообщение` в logs/logs.txt; в консоль — только по флагу."""
    LOG_FILE.parent.mkdir(exist_ok=True)
    line = f"[{_stamp()}] [{category}] {message}"
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(line + "\n")
    show = _echo if echo is None else echo
    if show:
        print(line)
