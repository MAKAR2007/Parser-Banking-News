"""
Конфигурация бота. Все значения берутся из переменных окружения (.env).
Секреты в коде не хранятся.

Важно: этой версии бота НЕ нужны api_id / api_hash. Посты читаются из публичной
веб-версии каналов (t.me/s/<канал>), а рассылка идёт через обычный Bot API.
Нужен только токен бота от @BotFather.
"""
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

# Сколько лучших постов отбирать в дневную сводку (топ по популярности).
# Если за день постов меньше — отправляются все найденные.
DIGEST_TARGET = int(os.getenv("DIGEST_TARGET", "25"))
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
