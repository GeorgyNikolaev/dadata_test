"""Web Search: Exa (основной, отдаёт полный текст страниц) и DuckDuckGo (второй индекс, сниппеты)."""
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import cache, config
from .egrul import Company
from .models import SearchHit

log = logging.getLogger(__name__)

_EXA_URL = "https://api.exa.ai/search"
_EXA_TEXT_CHARS = 12000


def initial_queries(c: Company) -> list[str]:
    """Шаблонные запросы первой итерации (без LLM)."""
    city = c.city or ""
    if c.type == "INDIVIDUAL":
        fio = c.names[0] if c.names else c.short_name
        return [f'"{c.inn}"', f"ИП {fio} {city}".strip(), f"ИП {fio} ИНН {c.inn} сайт"]
    queries = [f'"{c.inn}"']
    if c.ogrn:
        queries.append(f'"{c.ogrn}"')
    queries.append(f"{c.short_name} {city} официальный сайт".strip())
    return queries


def exa_search(query: str) -> list[SearchHit]:
    key = ("exa", query, config.SEARCH_RESULTS)
    data = cache.get(key)
    if data is None:
        r = httpx.post(
            _EXA_URL,
            headers={"x-api-key": config.EXA_TOKEN, "Content-Type": "application/json"},
            json={
                "query": query,
                "type": "auto",
                "numResults": config.SEARCH_RESULTS,
                "userLocation": "RU",
                "contents": {"text": {"maxCharacters": _EXA_TEXT_CHARS}},
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json().get("results") or []
        cache.put(key, data)
    return [
        SearchHit(url=x["url"], title=x.get("title") or "", text=x.get("text") or "",
                  query=query, engine="exa", rank=i)
        for i, x in enumerate(data)
    ]


def ddg_search(query: str) -> list[SearchHit]:
    key = ("ddg", query, config.SEARCH_RESULTS)
    data = cache.get(key)
    if data is None:
        try:
            from ddgs import DDGS
            data = DDGS().text(query, region="ru-ru", max_results=config.SEARCH_RESULTS) or []
        except Exception as e:  # неофициальный API: rate limit, смена разметки и т.п.
            log.warning("DuckDuckGo недоступен (%s): %s", query, e)
            return []
        cache.put(key, data)
    return [
        SearchHit(url=x.get("href") or "", title=x.get("title") or "", text=x.get("body") or "",
                  query=query, engine="ddg", rank=i)
        for i, x in enumerate(data) if x.get("href")
    ]


def _exa_safe(q: str) -> list[SearchHit]:
    try:
        return exa_search(q)
    except httpx.HTTPError as e:
        log.warning("Exa: ошибка на запросе %r: %s", q, e)
        return []


def search(queries: list[str]) -> list[SearchHit]:
    # Exa-запросы — параллельно; DuckDuckGo — последовательно (чувствителен к частоте запросов).
    with ThreadPoolExecutor(max_workers=4) as ex:
        exa_future = ex.map(_exa_safe, queries)
        ddg_hits = [h for q in queries for h in ddg_search(q)] if config.USE_DDG else []
        exa_hits = [h for hits in exa_future for h in hits]
    return exa_hits + ddg_hits
