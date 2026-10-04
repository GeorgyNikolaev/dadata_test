"""Нормализация доменов и извлечение их из текста."""
import re
from urllib.parse import urlsplit

import tldextract

# Офлайн-снимок Public Suffix List — без сетевых запросов при старте.
# Приватная часть PSL нужна для российских зон третьего уровня (msk.ru, spb.ru, com.ru):
# site.msk.ru — самостоятельный сайт, а не раздел msk.ru.
_extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)
_RU_ZONES = (".ru", ".su", ".рф", ".xn--p1ai")

# Зоны, в которых ищем домены в свободном тексте. Белый список нужен, чтобы
# "readme.md" или "photo.jpg" не принимались за домены. Зоны .москва нет намеренно:
# иначе адрес "г.Москва" превращается в домен.
_TLDS = (
    "ru su рф xn--p1ai com net org biz info pro online site store shop "
    "tech io ai group company moscow spb me by kz uz digital agency center club tv cloud "
    "app dev team studio space website travel"
).split()
_TLD_RE = "|".join(sorted((re.escape(t) for t in _TLDS), key=len, reverse=True))
_DOMAIN_RE = re.compile(
    rf"(?<![\w.-])((?:[a-zа-яё0-9](?:[a-zа-яё0-9-]{{0,61}}[a-zа-яё0-9])?\.)+(?:{_TLD_RE}))(?![\w-])",
    re.IGNORECASE,
)

# Площадки, которые почти никогда не являются сайтом конкретной компании:
# агрегаторы реквизитов, соцсети, маркетплейсы, карты, бесплатная почта, госсайты.
# Их не исключаем совсем (у Ozon или HeadHunter это и есть собственный сайт),
# а понижаем в ранжировании: проверка всё равно отсечёт чужой домен, потому что
# на его страницах нет ИНН нашей компании.
LOW_PRIORITY = set("""
rusprofile.ru list-org.com checko.ru zachestnyibiznes.ru saby.ru sbis.ru audit-it.ru
rbc.ru spark-interfax.ru kontur.ru reputation.ru damia.ru
readyratios.com buxbalans.ru vbankcenter.ru synapsenet.ru rusprofile.com egrul.ru
ogrn.online e-ecolog.ru checkko.ru nalog.ru nalog.gov.ru zakupki.gov.ru gosuslugi.ru
fedresurs.ru arbitr.ru tbank.ru sravni.ru moneyman.ru orgpage.ru
spravker.ru yell.ru zoon.ru flamp.ru 2gis.ru yandex.ru ya.ru google.com google.ru
vk.com vk.ru ok.ru t.me telegram.me facebook.com instagram.com youtube.com rutube.ru
dzen.ru ozon.ru wildberries.ru avito.ru hh.ru superjob.ru
mail.ru gmail.com bk.ru inbox.ru list.ru yandex.com rambler.ru icloud.com outlook.com
wikipedia.org github.com github.io tilda.ws tilda.cc wixsite.com blogspot.com myshopify.com
ucoz.ru narod.ru
""".split())


def registrable(host_or_url: str) -> str | None:
    """'https://www.Shop.Example.ru/a' -> 'example.ru'; кириллица остаётся кириллицей."""
    s = host_or_url.strip().lower()
    host = urlsplit(s).hostname if "://" in s else s.split("/")[0]
    if not host:
        return None
    if "xn--" in host:
        try:
            host = host.encode("ascii").decode("idna")
        except UnicodeError:
            pass
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    if ext.is_private and not ext.suffix.endswith(_RU_ZONES):
        # Поддомен хостинг-платформы (*.github.io, *.wixsite.com): сайт на чужом домене
        # мы не считаем сайтом организации — сводим к домену платформы.
        return ext.suffix
    return f"{ext.domain}.{ext.suffix}"


def domains_in_text(text: str) -> list[str]:
    """Все регистрируемые домены, упомянутые в тексте (в т.ч. из e-mail), без повторов."""
    seen: dict[str, None] = {}
    for m in _DOMAIN_RE.finditer(text):
        d = registrable(m.group(1))
        if d:
            seen.setdefault(d, None)
    return list(seen)


def is_low_priority(domain: str) -> bool:
    return domain in LOW_PRIORITY
