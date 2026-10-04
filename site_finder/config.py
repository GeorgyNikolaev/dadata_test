"""Настройки: ключи из .env, пути, параметры пайплайна."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DADATA_TOKEN = os.getenv("DADATA_TOKEN", "")
EXA_TOKEN = os.getenv("EXA_TOKEN", "")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max")
# Сертификат НУЦ Минцифры: без него TLS-проверка к API GigaChat не проходит.
RUSSIAN_CA = ROOT / "certs" / "russian_trusted_root_ca.pem"

CACHE_DIR = ROOT / ".cache"
LOGS_DIR = ROOT / "logs"
DIRECTORY_PATH = ROOT / "data" / "directory.csv"

USE_DDG = os.getenv("USE_DDG", "1") == "1"   # второй поисковик (бесплатный, неофициальный API)
SEARCH_RESULTS = 8                           # результатов на один запрос
MAX_CANDIDATES = 8                           # сколько доменов проверяем
MAX_SUBPAGES = 3                             # контакты/реквизиты/о компании/политика
HTTP_TIMEOUT = 8.0
NOT_FOUND_TTL_DAYS = 30                      # через сколько дней перепроверять "сайта нет"
