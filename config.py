"""
Конфигурация бота. Все значения берутся из переменных окружения (.env).
Ничего секретного в коде не хранится.
"""
import os
import sys

from dotenv import load_dotenv

# Загружаем .env из папки проекта
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


# --- Обязательные параметры ---
# api_id / api_hash берутся с https://my.telegram.org -> API development tools
try:
    API_ID = int(_require("API_ID"))
except ValueError:
    print("[CONFIG ERROR] API_ID должен быть числом.", file=sys.stderr)
    sys.exit(1)

API_HASH = _require("API_HASH")
BOT_TOKEN = _require("BOT_TOKEN")

# --- Необязательные параметры (с разумными значениями по умолчанию) ---
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow").strip()

# Час и минута ежедневной отправки сводки (по TIMEZONE)
try:
    SEND_HOUR = int(os.getenv("SEND_HOUR", "8"))
    SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))
except ValueError:
    SEND_HOUR, SEND_MINUTE = 8, 0

# Имя файла пользовательской сессии Telethon (создаётся через login.py)
USER_SESSION = os.getenv("USER_SESSION", "user_session")
BOT_SESSION = os.getenv("BOT_SESSION", "bot_session")

# Список каналов (username без @). Можно переопределить через переменную CHANNELS
# (через запятую), иначе используется список по умолчанию.
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

# Файл со списком подписчиков (chat_id), которым уходит сводка
SUBSCRIBERS_FILE = os.getenv("SUBSCRIBERS_FILE", "subscribers.json")

# Ограничение на число просматриваемых сообщений в одном канале (защита от
# бесконечного цикла на очень активных каналах)
MAX_MESSAGES_PER_CHANNEL = int(os.getenv("MAX_MESSAGES_PER_CHANNEL", "400"))
