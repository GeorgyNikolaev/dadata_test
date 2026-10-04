"""Пайплайн: ИНН -> {"domain": ..., "sites": [...]}.

Workflow с агентным шагом: шаги задаёт код, LLM принимает решения в двух точках —
вердикт по кандидатам и (если подтверждённого сайта нет) новые поисковые запросы.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from . import candidates as cand
from . import config, llm, rules, search, verify
from .directory import Directory
from .domains import registrable
from .egrul import CompanyNotFound, get_company
from .inn import is_valid_inn
from .matching import name_variants, quote_supported
from .models import Candidate

log = logging.getLogger(__name__)


def _dedup_redirects(checked: list[Candidate]) -> list[Candidate]:
    """a.ru -> редирект на b.ru: оставляем один кандидат на конечный домен."""
    by_final: dict[str, Candidate] = {}
    for c in checked:
        key = c.final_domain or c.domain
        if key in by_final:
            keep = by_final[key]
            keep.sources |= c.sources
            keep.mentioned_on |= c.mentioned_on
            keep.score += c.score
        else:
            by_final[key] = c
    return list(by_final.values())


def find_site(
    inn: str,
    *,
    use_llm: bool = True,
    max_iterations: int = 2,
    directory: Directory | None = None,
    refresh: bool = False,
) -> dict:
    inn = inn.strip()
    if not is_valid_inn(inn):
        raise ValueError(f"Некорректный ИНН: {inn!r}")

    if directory is not None and not refresh:
        known = directory.get(inn)
        if known is not None:
            return known

    started = time.time()
    trace: dict = {"inn": inn, "use_llm": use_llm, "max_iterations": max_iterations, "iterations": []}
    try:
        company = get_company(inn)
    except CompanyNotFound:
        result = {"domain": None, "sites": []}
        trace.update(error="ИНН не найден в ЕГРЮЛ/ЕГРИП", result=result)
        _write_trace(inn, trace)
        return result
    trace["company"] = asdict(company)
    names = name_variants(company.names, company.type, company.short_name)

    pool: dict[str, Candidate] = {}
    checked: list[Candidate] = []
    tried: list[str] = []
    queries = search.initial_queries(company)
    tokens = 0
    pages_by_domain: dict[str, list[dict]] = {}

    for iteration in range(1, max_iterations + 1):
        t0 = time.time()
        hits = search.search(queries)
        tried += queries
        t_search = time.time() - t0
        for h in hits:
            d = registrable(h.url)
            # Страницы домена из выдачи — дополнительные доказательства для проверки.
            # Карточки агрегаторов не берём: ИНН на "/id/3197573" не делает rusprofile.ru
            # сайтом компании. У DuckDuckGo только короткий сниппет — тоже не берём.
            if d and h.engine == "exa" and not cand.is_catalog_page(h.url, h.text, company, h.title):
                pages_by_domain.setdefault(d, []).append({"url": h.url, "text": h.text})
        cand.collect(company, hits, pool)
        batch = cand.top(pool, config.MAX_CANDIDATES, exclude={c.domain for c in checked})

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda c: verify.verify(c, company, names, pages_by_domain.get(c.domain, [])), batch))
        batch = _dedup_redirects(batch)
        t_verify = time.time() - t0 - t_search

        if use_llm:
            fetched = [c for c in batch if c.fetched]
            tokens += llm.judge(company, fetched)
            for c in fetched:
                # ИНН/ОГРН, найденные кодом на сайте, считаются подтверждёнными, даже если
                # модель взяла их в цитату из карточки, а не из показанного ей фрагмента.
                found = [n for n, f in ((company.inn, c.inn_found), (company.ogrn, c.ogrn_found)) if f and n]
                c.quote_ok = quote_supported(c.evidence_quote, c.evidence_text() + " " + " ".join(found))
            for c in batch:
                rules.decide(c)
        else:
            for c in batch:
                rules.decide_without_llm(c)
        checked += batch
        t_llm = time.time() - t0 - t_search - t_verify

        trace["iterations"].append({
            "queries": queries,
            "seconds": {"search": round(t_search, 1), "verify": round(t_verify, 1), "llm": round(t_llm, 1)},
            "hits": [{"engine": h.engine, "query": h.query, "rank": h.rank, "url": h.url} for h in hits],
            "candidates": [_cand_dict(c) for c in batch],
        })
        if any(c.accepted for c in checked) or iteration == max_iterations or not use_llm:
            break
        queries, t = llm.new_queries(company, tried, checked)
        tokens += t
        if not queries:
            break

    accepted = [c for c in checked if c.accepted]
    main = rules.pick_main(accepted)
    sites: list[str] = []
    for c in ([main] if main else []) + accepted:
        d = c.final_domain or c.domain
        if d not in sites:
            sites.append(d)
    result = {"domain": sites[0] if sites else None, "sites": sites}

    trace.update(result=result, llm_tokens=tokens, seconds=round(time.time() - started, 1))
    _write_trace(inn, trace)
    if directory is not None:
        directory.put(inn, result["domain"], result["sites"])
    return result


def _cand_dict(c: Candidate) -> dict:
    d = asdict(c)
    d["sources"] = sorted(c.sources)
    d["mentioned_on"] = sorted(c.mentioned_on)
    return d


def _write_trace(inn: str, trace: dict) -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOGS_DIR / f"{inn}.json", "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)
