"""Проверка кандидата: загрузка главной и страниц с реквизитами, поиск ИНН/ОГРН/названия.

Страницы качаем сами (httpx). Если сайт закрыт от ботов или рендерится JS-ом
(текста почти нет), берём текст через Exa contents.
"""
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from . import cache, config
from .domains import registrable
from .egrul import Company
from .matching import find_requisites
from .models import Candidate

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Ссылки на страницы с реквизитами, в порядке приоритета.
_SUBPAGE_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"реквизит|requisit|rekvizit",
        r"контакт|contact|kontakt",
        r"о компании|о нас|about|o-kompanii|/company/?$",
        r"политик|конфиденц|персональн|privacy|policy|politika",
        r"оферт|oferta|offer",
    )
]
_GUESS_PATHS = ["/contacts", "/kontakty", "/about", "/rekvizity"]
_MIN_TEXT = 300
_MAX_PAGE_CHARS = 60000
_MAX_BYTES = 3_000_000
_PAGE_DEADLINE = 15.0   # секунд на одну страницу
_SITE_BUDGET = 30.0     # секунд на весь сайт (главная + подстраницы)


def _parse(html: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    tree = LexborHTMLParser(html, encoding=True)  # кодировку (utf-8 / windows-1251) определяет сам
    title = (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")[:200]
    links = [(a.attributes.get("href") or "", a.text(strip=True)) for a in tree.css("a")]
    for tag in tree.css("script, style, noscript, svg"):
        tag.decompose()
    body = tree.body or tree.root
    text = body.text(separator=" ", strip=True) if body else ""
    return title, text[:_MAX_PAGE_CHARS], links


def _pick_subpages(base_url: str, links: list[tuple[str, str]], site: str) -> list[str]:
    found: list[str] = []
    for pattern in _SUBPAGE_PATTERNS:
        for href, label in links:
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            url = urljoin(base_url, href).split("#")[0]
            if registrable(url) != site or url.rstrip("/") == base_url.rstrip("/"):
                continue
            if (pattern.search(label) or pattern.search(urlsplit(url).path)) and url not in found:
                found.append(url)
                break  # одна страница на категорию
        if len(found) >= config.MAX_SUBPAGES:
            break
    return found


def _get(client: httpx.Client, url: str) -> tuple[str, bytes] | None:
    """GET с общим дедлайном и лимитом размера: таймаут httpx действует на каждую операцию
    чтения, и сервер, отдающий страницу по байту, может держать соединение бесконечно."""
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                return None
            buf = bytearray()
            started = time.monotonic()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > _MAX_BYTES or time.monotonic() - started > _PAGE_DEADLINE:
                    break
            return str(r.url), bytes(buf)
    except httpx.HTTPError as e:
        log.debug("fetch %s: %s", url, e)
    return None


def _fetch_http(domain: str) -> dict | None:
    with httpx.Client(follow_redirects=True, timeout=config.HTTP_TIMEOUT,
                      headers={"User-Agent": _UA, "Accept-Language": "ru,en;q=0.8"}) as client:
        started = time.monotonic()
        home = None
        for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
            home = _get(client, url)
            if home or time.monotonic() - started > _SITE_BUDGET:
                break
        if not home:
            return None
        final_url, html = home
        site = registrable(final_url) or domain
        title, text, links = _parse(html)
        pages = [{"url": final_url, "text": text}]
        for sub in _pick_subpages(final_url, links, site):
            if time.monotonic() - started > _SITE_BUDGET:
                break
            r = _get(client, sub)
            if r:
                pages.append({"url": r[0], "text": _parse(r[1])[1]})
        return {"final_domain": site, "title": title, "pages": pages, "via": "http"}


def _fetch_exa(domain: str) -> dict | None:
    urls = [f"https://{domain}"] + [f"https://{domain}{p}" for p in _GUESS_PATHS]
    try:
        r = httpx.post(
            "https://api.exa.ai/contents",
            headers={"x-api-key": config.EXA_TOKEN, "Content-Type": "application/json"},
            json={"urls": urls, "text": {"maxCharacters": 20000}},
            timeout=60,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.debug("exa contents %s: %s", domain, e)
        return None
    results = [x for x in r.json().get("results") or [] if x.get("text")]
    if not results:
        return None
    return {
        "final_domain": domain,
        "title": results[0].get("title") or "",
        "pages": [{"url": x["url"], "text": x["text"]} for x in results],
        "via": "exa",
    }


def fetch_site(domain: str) -> dict | None:
    key = ("site", domain)
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    site = _fetch_http(domain)
    if not site or sum(len(p["text"]) for p in site["pages"]) < _MIN_TEXT:
        site = _fetch_exa(domain) or site
    cache.put(key, site or {})
    return site


def verify(c: Candidate, company: Company, names: list[str], search_pages: list[dict]) -> None:
    """search_pages — страницы этого домена из поисковой выдачи (url, text). Поисковик
    часто находит именно "Контакты" или "О компании", и текст у нас уже есть бесплатно;
    кроме того, это выручает, когда сайт закрыт от ботов."""
    site = fetch_site(c.domain)
    pages = list(site["pages"]) if site else []
    seen = {p["url"].rstrip("/") for p in pages}
    pages += [p for p in search_pages if p["url"].rstrip("/") not in seen and p["text"]]
    if not pages:
        return
    c.fetched = True
    c.fetch_via = site["via"] if site else "search"
    c.final_domain = site["final_domain"] if site else c.domain
    c.title = site["title"] if site else ""
    c.pages = [p["url"] for p in pages]
    c.intro = pages[0]["text"][:400]
    with_req: list[str] = []
    name_only: list[str] = []
    for p in pages:
        m = find_requisites(p["text"], company.inn, company.ogrn, names, max_snippets=2)
        c.inn_found |= m.inn_found
        c.ogrn_found |= m.ogrn_found
        c.name_found |= m.name_found
        (with_req if (m.inn_found or m.ogrn_found) else name_only).extend(
            f"[{p['url']}] {s}" for s in m.snippets)
    # Фрагменты со страниц, где есть ИНН/ОГРН, важнее для LLM, чем упоминания названия.
    c.snippets = (with_req + name_only)[:5]
