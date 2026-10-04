"""Правило принятия решения по кандидату.

LLM не выдаёт вероятность: она относит сайт к категории и приводит цитату.
Принимает решение прозрачное правило, которое настраивается на размеченной выборке.
"""
from .models import GROUP_SITE, OWN_SITE, Candidate


def decide(c: Candidate) -> None:
    if not c.fetched:
        c.accepted, c.decision = False, "сайт не удалось загрузить"
    elif c.verdict not in (OWN_SITE, GROUP_SITE):
        c.accepted, c.decision = False, f"вердикт LLM: {c.verdict}"
    elif not c.quote_ok:
        c.accepted, c.decision = False, "цитата LLM не подтверждается текстом страницы"
    elif c.requisites_found:
        c.accepted, c.decision = True, "ИНН/ОГРН на сайте + подтверждение LLM"
    elif c.verdict == OWN_SITE and c.name_found and c.independent_signal:
        c.accepted, c.decision = True, "название на сайте + независимый источник + подтверждение LLM"
    else:
        c.accepted, c.decision = False, "недостаточно доказательств"


def decide_without_llm(c: Candidate) -> None:
    """Базовая линия для сравнения: только детерминированная проверка реквизитов."""
    c.accepted = c.fetched and c.requisites_found
    c.decision = "ИНН/ОГРН на сайте (без LLM)" if c.accepted else "реквизиты не найдены"


def pick_main(accepted: list[Candidate]) -> Candidate | None:
    """Основной сайт: сначала с реквизитами на сайте, затем — по силе сигнала из поиска."""
    if not accepted:
        return None
    return sorted(accepted, key=lambda c: (not c.requisites_found, c.verdict != OWN_SITE, -c.score, c.best_rank))[0]
