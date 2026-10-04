"""Сбор доменов-кандидатов из выдачи поиска и карточки ЕГРЮЛ."""
from .domains import domains_in_text, is_low_priority, registrable
from .egrul import Company
from .matching import find_requisites
from .models import Candidate, SearchHit

LOW_PRIORITY_FACTOR = 0.3


def collect(company: Company, hits: list[SearchHit], pool: dict[str, Candidate]) -> None:
    """Пополняет пул кандидатов.

    Источники:
    - домен результата поиска (+ бонус, если на странице есть ИНН/ОГРН — возможно, это и есть сайт);
    - домены, упомянутые в тексте страниц, где есть ИНН/ОГРН компании (карточки агрегаторов:
      "Сайт: …", "E-mail: info@…") — независимый от самого сайта сигнал;
    - домены корпоративной почты из ЕГРЮЛ.
    """
    def add(domain: str, source: str, weight: float, rank: int = 10**6) -> Candidate:
        c = pool.setdefault(domain, Candidate(domain=domain))
        c.sources.add(source)
        c.score += weight * (LOW_PRIORITY_FACTOR if is_low_priority(domain) else 1.0)
        c.best_rank = min(c.best_rank, rank)
        return c

    for h in hits:
        own = registrable(h.url)
        if not own:
            continue
        m = find_requisites(h.text, company.inn, company.ogrn, [])
        page_about_company = m.inn_found or m.ogrn_found
        add(own, "search_result", 2.0 if page_about_company else 1.0, h.rank)
        if page_about_company:
            for d in domains_in_text(h.text):
                if d != own:
                    add(d, "mention", 1.0).mentioned_on.add(h.url)

    for d in company.email_domains:
        dom = registrable(d)
        if dom and not is_low_priority(dom):
            add(dom, "egrul_email", 3.0)


def top(pool: dict[str, Candidate], n: int, exclude: set[str]) -> list[Candidate]:
    fresh = [c for c in pool.values() if c.domain not in exclude]
    return sorted(fresh, key=lambda c: (-c.score, c.best_rank))[:n]
