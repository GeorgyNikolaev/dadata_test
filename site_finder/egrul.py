"""Карточка организации из ЕГРЮЛ/ЕГРИП.

В прототипе источник — API DaData (findById/party). В продакшене внутри DaData
это join с собственной таблицей реестра, без сетевого запроса.
"""
from dataclasses import dataclass, field

import httpx

from . import cache, config

_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


@dataclass
class Company:
    inn: str
    ogrn: str | None
    type: str                      # LEGAL | INDIVIDUAL
    full_name: str                 # 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ДЕЙТА КЬЮ"'
    short_name: str                # 'ООО "ДЕЙТА КЬЮ"'
    names: list[str]               # названия без ОПФ для поиска и сверки: ['ДЕЙТА КЬЮ', 'Дейта Кью']
    city: str | None
    region: str | None
    address: str | None
    status: str | None             # ACTIVE | LIQUIDATED | ...
    okved: str | None
    email_domains: list[str] = field(default_factory=list)  # есть только на платных тарифах

    def card(self) -> str:
        """Короткое текстовое описание для промпта LLM."""
        kind = "ИП" if self.type == "INDIVIDUAL" else "Юрлицо"
        parts = [
            f"{kind}: {self.short_name}",
            f"Полное название: {self.full_name}",
            f"ИНН: {self.inn}, ОГРН: {self.ogrn}",
            f"Адрес: {self.address}",
            f"Статус: {self.status}, ОКВЭД: {self.okved}",
        ]
        if self.email_domains:
            parts.append(f"Домены e-mail из ЕГРЮЛ: {', '.join(self.email_domains)}")
        return "\n".join(parts)


class CompanyNotFound(Exception):
    pass


def _fetch_raw(inn: str) -> dict | None:
    key = ("dadata", inn)
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    r = httpx.post(
        _URL,
        json={"query": inn, "branch_type": "MAIN"},
        headers={"Authorization": f"Token {config.DADATA_TOKEN}", "Accept": "application/json"},
        timeout=config.HTTP_TIMEOUT,
    )
    r.raise_for_status()
    sugg = r.json().get("suggestions") or []
    data = sugg[0]["data"] if sugg else {}
    cache.put(key, data)
    return data or None


def get_company(inn: str) -> Company:
    d = _fetch_raw(inn)
    if not d:
        raise CompanyNotFound(inn)
    name = d.get("name") or {}
    names: list[str] = []
    for n in (name.get("short"), name.get("full"), name.get("latin")):
        if n and n not in names:
            names.append(n)
    addr = d.get("address") or {}
    ad = addr.get("data") or {}
    emails = d.get("emails") or []
    email_domains = sorted({(e.get("data") or e).get("domain") for e in emails if (e.get("data") or e).get("domain")})
    return Company(
        inn=inn,
        ogrn=d.get("ogrn"),
        type=d.get("type") or "LEGAL",
        full_name=name.get("full_with_opf") or "",
        short_name=name.get("short_with_opf") or name.get("full_with_opf") or "",
        names=names,
        city=ad.get("city") or ad.get("settlement") or ad.get("region"),
        region=ad.get("region_with_type"),
        address=addr.get("unrestricted_value") or addr.get("value"),
        status=(d.get("state") or {}).get("status"),
        okved=d.get("okved"),
        email_domains=email_domains,
    )
