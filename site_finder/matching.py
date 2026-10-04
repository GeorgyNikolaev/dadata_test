"""Поиск ИНН, ОГРН и названия организации в тексте страницы + вырезка фрагментов."""
import re
from dataclasses import dataclass, field

_QUOTES = re.compile(r"[«»\"“”„'`]")
_SPACES = re.compile(r"\s+")
_OPF = re.compile(
    r"^(ооо|оао|зао|пао|ао|нао|ип|ано|нко|фгуп|гуп|муп|мкоу|мбоу|мбу|гбу|фгбу)\s+",
)
SNIPPET_RADIUS = 250


def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    t = _QUOTES.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def _digits_pattern(number: str) -> re.Pattern:
    # ИНН/ОГРН на сайтах иногда пишут с пробелами: "7721 581 040".
    return re.compile(r"(?<!\d)" + r"[  ]?".join(number) + r"(?!\d)")


def name_variants(names: list[str], company_type: str) -> list[str]:
    """Нормализованные варианты названия, по которым ищем на странице.

    Короткие (< 4 символов) отбрасываем: "Альфа" ещё можно, "АБВ" даёт ложные совпадения.
    Для ИП добавляем форму "Фамилия И. О." / "Фамилия И.О."."""
    out: list[str] = []
    for n in names:
        v = _OPF.sub("", normalize(n))
        if len(v) >= 4 and v not in out:
            out.append(v)
        if company_type == "INDIVIDUAL":
            parts = v.split()
            if len(parts) >= 2:
                initials = [p[0] + "." for p in parts[1:]]
                for form in (f"{parts[0]} {' '.join(initials)}", f"{parts[0]} {''.join(initials)}"):
                    if form not in out:
                        out.append(form)
    return out


@dataclass
class Match:
    inn_found: bool = False
    ogrn_found: bool = False
    name_found: bool = False
    snippets: list[str] = field(default_factory=list)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans.sort()
    out: list[tuple[int, int]] = []
    for s, e in spans:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def find_requisites(text: str, inn: str, ogrn: str | None, names: list[str], max_snippets: int = 4) -> Match:
    """Ищет реквизиты в тексте и возвращает флаги + фрагменты вокруг совпадений."""
    flat = _SPACES.sub(" ", text)
    m = Match()
    spans: list[tuple[int, int]] = []

    for hit in _digits_pattern(inn).finditer(flat):
        m.inn_found = True
        spans.append(hit.span())
    if ogrn:
        for hit in _digits_pattern(ogrn).finditer(flat):
            m.ogrn_found = True
            spans.append(hit.span())

    # Нормализация с сохранением длины (позиции совпадений = позиции в исходном тексте).
    norm = _QUOTES.sub(" ", flat.lower().replace("ё", "е"))
    name_hits = 0
    if len(norm) == len(flat):
        for v in names:
            pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in v.split()) + r"(?!\w)"
            for hit in re.finditer(pattern, norm):
                m.name_found = True
                name_hits += 1
                spans.append(hit.span())

    req_spans = spans[: len(spans) - name_hits]
    windows = _merge([(max(0, s - SNIPPET_RADIUS), e + SNIPPET_RADIUS) for s, e in spans])
    # Сначала фрагменты с ИНН/ОГРН, затем — только с названием.
    windows.sort(key=lambda w: not any(w[0] <= s < w[1] for s, _ in req_spans))
    for s, e in windows[:max_snippets]:
        m.snippets.append(flat[s:e].strip())
    return m


_WORD = re.compile(r"[a-zа-я0-9]+")
_DIGIT_GAP = re.compile(r"(?<=\d)[ \u00a0-](?=\d)")


def quote_supported(quote: str, text: str, min_coverage: float = 0.85) -> bool:
    """Подтверждается ли цитата LLM текстом страницы (защита от выдуманных доказательств).

    Дословное совпадение требовать нельзя: модель переформатирует таблицы в списки, убирает
    разметку. Поэтому проверяем по словам: все числа из цитаты (ИНН, ОГРН, телефоны) должны
    быть в тексте, и не меньше min_coverage остальных слов — тоже."""
    q_words = [w for w in _WORD.findall(normalize(_DIGIT_GAP.sub("", quote))) if len(w) >= 3]
    if len(q_words) < 2:
        return False
    t_words = set(_WORD.findall(normalize(_DIGIT_GAP.sub("", text))))
    numbers = [w for w in q_words if w.isdigit() and len(w) >= 5]
    if any(n not in t_words for n in numbers):
        return False
    return sum(w in t_words for w in q_words) / len(q_words) >= min_coverage
