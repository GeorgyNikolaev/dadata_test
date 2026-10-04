"""Структуры данных пайплайна."""
from dataclasses import dataclass, field

# Вердикты LLM по кандидату.
OWN_SITE = "own_site"                    # собственный сайт организации
GROUP_SITE = "group_site"                # сайт группы/холдинга, где указаны реквизиты организации
THIRD_PARTY = "third_party_mention"      # организация лишь упомянута (контрагент, оператор ПДн, агрегатор)
UNRELATED = "unrelated"                  # сайт другой организации / недостаточно данных
VERDICTS = (OWN_SITE, GROUP_SITE, THIRD_PARTY, UNRELATED)
REFUSED = "llm_refused"                  # GigaChat отказался отвечать (контентный фильтр)


@dataclass
class SearchHit:
    url: str
    title: str
    text: str          # полный текст страницы (Exa) или сниппет (DuckDuckGo)
    query: str
    engine: str
    rank: int


@dataclass
class Candidate:
    domain: str
    score: float = 0.0
    sources: set[str] = field(default_factory=set)       # search_result | mention | egrul_email
    mentioned_on: set[str] = field(default_factory=set)  # страницы с ИНН/ОГРН компании, где упомянут домен
    best_rank: int = 10**6

    # Проверка (заполняет verify.py)
    final_domain: str | None = None    # после редиректов
    fetched: bool = False
    fetch_via: str | None = None       # http | exa
    pages: list[str] = field(default_factory=list)
    title: str = ""
    intro: str = ""                    # начало текста главной: о чём сайт
    inn_found: bool = False
    ogrn_found: bool = False
    name_found: bool = False
    snippets: list[str] = field(default_factory=list)

    # Оценка LLM
    verdict: str | None = None
    evidence_quote: str = ""
    quote_ok: bool = False

    # Решение
    accepted: bool = False
    decision: str = ""

    @property
    def requisites_found(self) -> bool:
        return self.inn_found or self.ogrn_found

    @property
    def independent_signal(self) -> bool:
        """Домен связан с компанией источником, независимым от самого сайта."""
        return "egrul_email" in self.sources or bool(self.mentioned_on)

    def evidence_text(self) -> str:
        return "\n".join([self.title, self.intro, *self.snippets])
