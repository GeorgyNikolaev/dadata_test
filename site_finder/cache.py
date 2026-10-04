"""Дисковый кэш ответов внешних сервисов (поиск, страницы, ЕГРЮЛ, LLM).

Экономит лимиты и делает прогоны воспроизводимыми: повторный запуск оценки
на тех же ИНН не тратит ни запросов, ни токенов. Отключается SITE_FINDER_NO_CACHE=1.
"""
import hashlib
import json
import os
from typing import Any

from . import config

_ENABLED = os.getenv("SITE_FINDER_NO_CACHE") != "1"


def _path(key: tuple) -> "os.PathLike":
    h = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return config.CACHE_DIR / str(key[0]) / f"{h}.json"


def get(key: tuple) -> Any | None:
    if not _ENABLED:
        return None
    p = _path(key)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def put(key: tuple, value: Any) -> None:
    if not _ENABLED:
        return
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
