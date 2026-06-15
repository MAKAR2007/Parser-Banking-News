"""
Конфигурация бота. Все значения берутся из переменных окружения (.env).
Секреты в коде не хранятся.

Важно: этой версии бота НЕ нужны api_id / api_hash. Посты читаются из публичной
веб-версии каналов (t.me/s/<канал>), а рассылка идёт через обычный Bot API.
Нужен только токен бота от @BotFather.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(
            f"[CONFIG ERROR] Не задана обязательная переменная окружения {name}.\n"
            f"Заполните файл .env (см. .env.example) и запустите снова.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


# --- Обязательное ---
BOT_TOKEN = _require("BOT_TOKEN")

# --- Необязательное (с разумными значениями по умолчанию) ---
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow").strip()

try:
    SEND_HOUR = int(os.getenv("SEND_HOUR", "8"))
    SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))
except ValueError:
    SEND_HOUR, SEND_MINUTE = 8, 0

# Список каналов (username без @). Можно переопределить через CHANNELS в .env.
_DEFAULT_CHANNELS = [
    "prbezposhady",
    "blogbankir",
    "karaulny_accountant",
    "ecotopor",
    "banksta",
    "bankrollo",
    "finkrolik",
    "smmrus",
    "sell_me",
    "lider",
    "boomers_TV",
    "sale_caviar",
    "whackdoor",
    "trendsetter",
    "concertzaal",
]

_env_channels = os.getenv("CHANNELS", "").strip()
if _env_channels:
    CHANNELS = [c.strip().lstrip("@") for c in _env_channels.split(",") if c.strip()]
else:
    CHANNELS = _DEFAULT_CHANNELS

# Параметры саммаризации
SUMMARY_MAX_SENTENCES = int(os.getenv("SUMMARY_MAX_SENTENCES", "2"))
SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "400"))

# Сколько постов в каждом слоте дайджеста.
# Слот A — экономика (важные/понятные всем): высокая релевантность × охват.
ECON_SLOTS = int(os.getenv("ECON_SLOTS", "5"))
# Слот B — интересное (вирусные посты для широкой аудитории): охват в приоритете.
GENERAL_SLOTS = int(os.getenv("GENERAL_SLOTS", "15"))
# Устаревший параметр, оставлен для совместимости с логами.
DIGEST_TARGET = ECON_SLOTS + GENERAL_SLOTS
# Максимум постов от одного канала в сводке (для разнообразия топа).
MAX_PER_CHANNEL = int(os.getenv("MAX_PER_CHANNEL", "5"))
# Минимальная длина текста, чтобы пост считался «содержательным» для топа.
MIN_TEXT_LEN = int(os.getenv("MIN_TEXT_LEN", "40"))

# Файл со списком подписчиков (chat_id)
SUBSCRIBERS_FILE = os.getenv("SUBSCRIBERS_FILE", "subscribers.json")

# Ограничения парсера веб-версии
# Сколько страниц (по ~20 постов) максимум листать назад в одном канале.
MAX_PAGES_PER_CHANNEL = int(os.getenv("MAX_PAGES_PER_CHANNEL", "30"))
# Пауза между запросами к t.me, сек (бережём сайт и не ловим лимиты).
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.7"))
# Сколько раз повторять запрос страницы при сетевой ошибке.
# Через прокси (WARP) сбои возвращаются мгновенно, поэтому повторы дёшевы и их
# можно делать много — это перекрывает «плохие окна» нестабильного канала.
REQUEST_RETRIES = int(os.getenv("REQUEST_RETRIES", "12"))

# Сколько раз повторять вызов Bot API (getMe/sendMessage/getUpdates) при сбое.
API_RETRIES = int(os.getenv("API_RETRIES", "8"))

# Необязательный прокси для ВСЕГО трафика бота (и t.me, и api.telegram.org).
# Нужен, если сервер не имеет прямого доступа к Telegram.
# Примеры: http://user:pass@host:port  |  socks5://user:pass@host:port
PROXY_URL = os.getenv("PROXY_URL", "").strip()
