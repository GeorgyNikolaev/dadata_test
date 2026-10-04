"""CLI: python -m site_finder 7721581040  или  python -m site_finder '{"inn": "7721581040"}'"""
import argparse
import json
import logging
import sys

from . import config
from .directory import Directory
from .pipeline import find_site
from .search import SearchUnavailable


def _parse_inn(raw: str) -> str:
    raw = raw.strip()
    return json.loads(raw)["inn"] if raw.startswith("{") else raw


def main() -> None:
    p = argparse.ArgumentParser(description="Поиск основного сайта организации по ИНН")
    p.add_argument("inn", nargs="+", help='ИНН или JSON {"inn": "..."}; можно несколько')
    p.add_argument("--refresh", action="store_true", help="искать заново, даже если ИНН есть в справочнике")
    p.add_argument("--no-directory", action="store_true", help="не читать и не писать справочник")
    p.add_argument("--no-llm", action="store_true", help="без LLM: только проверка реквизитов (базовая линия)")
    p.add_argument("-v", "--verbose", action="store_true", help="показать кандидатов и решения (stderr)")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    directory = None if a.no_directory else Directory()
    for raw in a.inn:
        inn = _parse_inn(raw)
        try:
            result = find_site(inn, use_llm=not a.no_llm, directory=directory, refresh=a.refresh)
        except (ValueError, SearchUnavailable) as e:
            # Ошибку не превращаем в {"domain": null}: это был бы ложный ответ "сайта нет".
            print(json.dumps({"inn": inn, "error": str(e)}, ensure_ascii=False))
            continue
        print(json.dumps(result if len(a.inn) == 1 else {"inn": inn, **result}, ensure_ascii=False))
        if a.verbose:
            _print_trace(inn)


def _print_trace(inn: str) -> None:
    path = config.LOGS_DIR / f"{inn}.json"
    if not path.exists():
        print("  (ответ из справочника, поиск не выполнялся)", file=sys.stderr)
        return
    t = json.loads(path.read_text(encoding="utf-8"))
    for i, it in enumerate(t.get("iterations", []), 1):
        print(f"  Итерация {i}. Запросы: {it['queries']}  Время, с: {it.get('seconds')}", file=sys.stderr)
        for c in it["candidates"]:
            flags = "".join(s for s, f in (("И", c["inn_found"]), ("О", c["ogrn_found"]), ("Н", c["name_found"])) if f)
            mark = "+" if c["accepted"] else "-"
            print(f"   {mark} {c['domain']:<32} [{flags:<3}] {c['verdict'] or '':<20} {c['decision']}", file=sys.stderr)


if __name__ == "__main__":
    main()
