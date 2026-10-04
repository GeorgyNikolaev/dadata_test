"""Справочник ИНН -> сайты (CSV).

Хранит и найденные сайты, и отрицательные результаты: компании без сайта не ищутся
повторно в течение NOT_FOUND_TTL_DAYS. CSV выбран, чтобы справочник можно было открыть
в Excel и перенести в другую систему одной загрузкой.
"""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

FIELDS = ["inn", "domain", "sites", "checked_at"]


class Directory:
    def __init__(self, path: Path = config.DIRECTORY_PATH):
        self.path = path
        self.rows: dict[str, dict] = {}
        if path.exists():
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    self.rows[row["inn"]] = row

    def get(self, inn: str, now: datetime | None = None) -> dict | None:
        """Ответ из справочника или None, если ИНН нужно (пере)искать."""
        row = self.rows.get(inn)
        if row is None:
            return None
        if not row["domain"]:
            now = now or datetime.now(timezone.utc)
            if now - datetime.fromisoformat(row["checked_at"]) > timedelta(days=config.NOT_FOUND_TTL_DAYS):
                return None
        return {"domain": row["domain"] or None, "sites": [s for s in row["sites"].split(";") if s]}

    def put(self, inn: str, domain: str | None, sites: list[str]) -> None:
        self.rows[inn] = {
            "inn": inn,
            "domain": domain or "",
            "sites": ";".join(sites),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(self.rows[k] for k in sorted(self.rows))
        tmp.replace(self.path)
