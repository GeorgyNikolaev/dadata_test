"""Тесты частей пайплайна, не требующих сети."""
from datetime import datetime, timedelta, timezone

from site_finder import rules
from site_finder.candidates import is_catalog_page
from site_finder.egrul import Company
from site_finder.directory import Directory
from site_finder.domains import domains_in_text, registrable
from site_finder.inn import is_valid_inn
from site_finder.matching import find_requisites, name_variants, quote_supported
from site_finder.models import OWN_SITE, THIRD_PARTY, Candidate


def test_inn_checksum():
    assert is_valid_inn("7721581040")      # ЮЛ (DaData)
    assert is_valid_inn("391709034783")    # ИП
    assert not is_valid_inn("7721581041")
    assert not is_valid_inn("123")
    assert not is_valid_inn("77215810ab")


def test_registrable():
    assert registrable("https://www.Shop.Dadata.ru/api/") == "dadata.ru"
    assert registrable("xn--e1afmkfd.xn--p1ai") == "пример.рф"
    assert registrable("site.msk.ru") == "site.msk.ru"      # российская зона 3-го уровня
    assert registrable("shop.wixsite.com") == "wixsite.com"  # сайт на чужом домене -> платформа


def test_domains_in_text():
    text = "Сайт: www.dadata.ru, почта i**o@hflabs.ru; файл readme.md; см. Пример.РФ и nalog.gov.ru/egrul"
    found = domains_in_text(text)
    assert "dadata.ru" in found and "hflabs.ru" in found and "пример.рф" in found
    assert "readme.md" not in found
    assert "nalog.gov.ru" in found


def test_find_requisites_with_spaces_and_name():
    text = 'Контакты\nООО «Дейта Кью»\nИНН 7721 581 040, ОГРН 5077746329876\nМосква'
    m = find_requisites(text, "7721581040", "5077746329876", name_variants(["Дейта Кью"], "LEGAL"))
    assert m.inn_found and m.ogrn_found and m.name_found
    assert m.snippets and "7721 581 040" in m.snippets[0]


def test_inn_not_matched_inside_longer_number():
    m = find_requisites("счёт 4077215810400000", "7721581040", None, [])
    assert not m.inn_found


def test_individual_name_variants():
    v = name_variants(["Смирнов Александр Олегович"], "INDIVIDUAL")
    assert "смирнов александр олегович" in v and "смирнов а. о." in v and "смирнов а.о." in v


def test_quote_check():
    page = "| Полное наименование | Публичное акционерное общество «Сбербанк России» | | ИНН | 7 707 083 893 |"
    # таблица, переписанная моделью в список, — подтверждается
    assert quote_supported("Полное наименование: Публичное акционерное общество «Сбербанк России» - ИНН: 7707083893", page)
    # выдуманный ИНН — нет
    assert not quote_supported("Публичное акционерное общество «Сбербанк России» ИНН 7707083890", page)
    # выдуманный текст — нет
    assert not quote_supported("Официальный сайт ООО Ромашка, все права защищены", page)


def _cand(**kw) -> Candidate:
    c = Candidate(domain="example.ru", fetched=True, quote_ok=True, verdict=OWN_SITE)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_rules():
    c = _cand(inn_found=True); rules.decide(c); assert c.accepted
    # ИНН есть, но это чужая политика ПДн, где компания — третье лицо
    c = _cand(inn_found=True, verdict=THIRD_PARTY); rules.decide(c); assert not c.accepted
    # Выдуманная цитата
    c = _cand(inn_found=True, quote_ok=False); rules.decide(c); assert not c.accepted
    # Только название — нужен независимый источник
    c = _cand(name_found=True); rules.decide(c); assert not c.accepted
    c = _cand(name_found=True, mentioned_on={"https://saby.ru/x"}); rules.decide(c); assert c.accepted


def test_pick_main_prefers_requisites():
    a = _cand(domain="a.ru", name_found=True, score=10)
    b = _cand(domain="b.ru", inn_found=True, score=1)
    assert rules.pick_main([a, b]).domain == "b.ru"


def test_directory_roundtrip_and_ttl(tmp_path):
    p = tmp_path / "directory.csv"
    d = Directory(p)
    d.put("7721581040", "dadata.ru", ["dadata.ru"])
    d.put("391709034783", None, [])
    d2 = Directory(p)
    assert d2.get("7721581040") == {"domain": "dadata.ru", "sites": ["dadata.ru"]}
    assert d2.get("391709034783") == {"domain": None, "sites": []}
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert d2.get("391709034783", now=later) is None       # "сайта нет" перепроверяется
    assert d2.get("7721581040", now=later) is not None      # найденный сайт — нет


def test_catalog_page_detection():
    c = Company(inn="7721581040", ogrn="5077746329876", type="LEGAL", full_name="", short_name="",
                names=[], city=None, region=None, address=None, status=None, okved=None)
    assert is_catalog_page("https://vembo.ru/company/5077746329876", "", c)
    many = " ".join(["7721581040", "7707083893", "7736207543", "7710140679"])
    assert is_catalog_page("https://example.ru/x", many, c)
    assert not is_catalog_page("https://dadata.ru/contacts/", "ООО «Дейта Кью», ИНН 7721581040", c)
