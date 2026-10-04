"""LLM (GigaChat): вердикт по кандидатам и генерация новых поисковых запросов.

Строгий JSON получаем через function calling: модель обязана "вызвать" функцию
с аргументами по схеме. Ответы кэшируются (temperature=0 → воспроизводимость).
"""
import json
import logging

from gigachat import GigaChat

from . import cache, config
from .egrul import Company
from .models import REFUSED, VERDICTS, Candidate

log = logging.getLogger(__name__)

JUDGE_SYSTEM = """Ты проверяешь, какие из найденных сайтов принадлежат организации из карточки ЕГРЮЛ.
Опирайся только на приведённые фрагменты страниц, ничего не додумывай.

Для каждого кандидата выбери вердикт:
- own_site — собственный сайт этой организации: её реквизиты указаны как реквизиты владельца сайта / продавца / исполнителя, либо сайт явно о ней и её деятельности;
- group_site — сайт группы компаний или холдинга, где эта организация указана как одна из компаний группы со своими реквизитами;
- third_party_mention — организация лишь упомянута на чужом сайте: контрагент, партнёр, клиент, поставщик, оператор или обработчик персональных данных, банк, страница агрегатора/каталога/новости;
- unrelated — сайт другой организации или данных недостаточно.

Типичная ловушка: ИНН организации встречается в политике обработки персональных данных или в списке третьих лиц чужого сайта — это third_party_mention.
Совпадение названия без реквизитов — слабое доказательство: тезки и похожие названия встречаются часто, сверяй город и вид деятельности.

evidence_quote — дословная цитата (до 200 символов) из текста кандидата, на которой основан вердикт. Копируй символ в символ, не перефразируй."""

QUERIES_SYSTEM = """Ты помогаешь найти официальный сайт российской организации через веб-поиск.
Первые запросы не дали подтверждённого сайта. Предложи до 3 новых поисковых запросов, которые с большей вероятностью приведут на собственный сайт организации.
Идеи: бренд или торговая марка (может отличаться от юрназвания), латинское написание, сокращённое название, вид деятельности + город, ФИО предпринимателя + деятельность.
Не повторяй уже использованные запросы. Запросы на русском или латиницей, короткие."""

_JUDGE_FN = {
    "name": "submit_verdicts",
    "description": "Вернуть вердикт по каждому кандидату",
    "parameters": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "домен кандидата как в заголовке"},
                        "verdict": {"type": "string", "enum": list(VERDICTS)},
                        "evidence_quote": {"type": "string", "description": "дословная цитата из текста кандидата"},
                    },
                    "required": ["domain", "verdict", "evidence_quote"],
                },
            }
        },
        "required": ["results"],
    },
}

_QUERIES_FN = {
    "name": "submit_queries",
    "description": "Вернуть новые поисковые запросы",
    "parameters": {
        "type": "object",
        "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
        "required": ["queries"],
    },
}

_client: GigaChat | None = None


class LLMRefusal(Exception):
    """Модель не вызвала функцию: сработал фильтр GigaChat (finish_reason=blacklist) или сбой формата."""


def _gigachat() -> GigaChat:
    global _client
    if _client is None:
        _client = GigaChat(
            credentials=config.GIGACHAT_AUTHORIZATION_KEY,
            scope=config.GIGACHAT_SCOPE,
            model=config.GIGACHAT_MODEL,
            ca_bundle_file=str(config.RUSSIAN_CA),
            timeout=120,
        )
    return _client


def _call(system: str, user: str, fn: dict) -> dict:
    key = ("llm", config.GIGACHAT_MODEL, system, user, fn)
    cached = cache.get(key)
    if cached is not None:
        return cached
    resp = _gigachat().chat({
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "functions": [fn],
        "function_call": {"name": fn["name"]},
        "temperature": 0.0,
    })
    choice = resp.choices[0]
    msg = choice.message
    if not msg.function_call:
        raise LLMRefusal(f"finish_reason={choice.finish_reason}: {(msg.content or '')[:120]}")
    args = msg.function_call.arguments
    if isinstance(args, str):
        args = json.loads(args)
    usage = resp.usage.total_tokens if resp.usage else 0
    out = {"args": args, "tokens": usage}
    cache.put(key, out)  # отказы не кэшируем
    return out


def _candidate_block(i: int, c: Candidate) -> str:
    redirect = f" (редирект на {c.final_domain})" if c.final_domain and c.final_domain != c.domain else ""
    found = ", ".join(k for k, v in (("ИНН", c.inn_found), ("ОГРН", c.ogrn_found), ("название", c.name_found)) if v)
    snippets = "\n".join(f"- {s}" for s in c.snippets) or "- (реквизиты и название на страницах не найдены)"
    return (
        f"### Кандидат {i}: {c.domain}{redirect}\n"
        f"Заголовок: {c.title}\n"
        f"Начало главной страницы: {c.intro}\n"
        f"Найдено на сайте: {found or 'ничего'}\n"
        f"Фрагменты:\n{snippets}"
    )


def _judge_batch(company: Company, candidates: list[Candidate]) -> tuple[dict, int]:
    user = "Карточка ЕГРЮЛ:\n" + company.card() + "\n\n" + "\n\n".join(
        _candidate_block(i + 1, c) for i, c in enumerate(candidates)
    )
    out = _call(JUDGE_SYSTEM, user, _JUDGE_FN)
    by_domain = {str(r.get("domain", "")).lower().strip(): r for r in out["args"].get("results", [])}
    return by_domain, out["tokens"]


def judge(company: Company, candidates: list[Candidate]) -> int:
    """Проставляет verdict / evidence_quote кандидатам. Возвращает число потраченных токенов.

    Все кандидаты оцениваются одним запросом. Если GigaChat отказывается отвечать
    (фильтр срабатывает на "чувствительный" текст одной из страниц, например о санкциях),
    оцениваем каждого кандидата отдельно — отказ затронет только его."""
    if not candidates:
        return 0
    tokens = 0
    try:
        by_domain, tokens = _judge_batch(company, candidates)
    except LLMRefusal as e:
        log.warning("GigaChat отказал на пакете (%s), оцениваю кандидатов по одному", e)
        by_domain = {}
        for c in candidates:
            try:
                one, t = _judge_batch(company, [c])
                tokens += t
                by_domain.update(one)
            except LLMRefusal:
                c.verdict = REFUSED
    for c in candidates:
        if c.verdict == REFUSED:
            continue
        r = by_domain.get(c.domain) or by_domain.get(c.final_domain or "")
        if not r:
            c.verdict = "unrelated"
            continue
        v = r.get("verdict")
        c.verdict = v if v in VERDICTS else "unrelated"
        c.evidence_quote = r.get("evidence_quote") or ""
    return tokens


def new_queries(company: Company, tried: list[str], rejected: list[Candidate]) -> tuple[list[str], int]:
    lines = [f"- {c.domain}: {c.decision}" for c in rejected] or ["- (кандидатов не было)"]
    user = (
        "Карточка ЕГРЮЛ:\n" + company.card()
        + "\n\nУже использованные запросы:\n" + "\n".join(f"- {q}" for q in tried)
        + "\n\nПроверенные и отклонённые домены:\n" + "\n".join(lines)
    )
    try:
        out = _call(QUERIES_SYSTEM, user, _QUERIES_FN)
    except LLMRefusal as e:
        log.warning("GigaChat не сгенерировал запросы: %s", e)
        return [], 0
    queries = [q.strip() for q in out["args"].get("queries", []) if isinstance(q, str) and q.strip()]
    return [q for q in queries if q not in tried][:3], out["tokens"]
