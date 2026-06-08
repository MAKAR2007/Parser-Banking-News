"""
Главный модуль бота «Parser Banking News».

Что делает:
  1. Раз в сутки в заданное время (по умолчанию 08:00 по Москве) собирает все
     посты за прошедший календарный день из списка Telegram-каналов.
  2. Делает короткую выжимку (саммари) текста каждого поста.
  3. Отправляет всем подписчикам сводку в формате: «сокращённый текст + ссылка».

Архитектура:
  * USER_CLIENT (Telethon, пользовательский аккаунт) — ЧИТАЕТ каналы.
    Боту BotFather история чужих каналов недоступна, поэтому чтение идёт через
    обычный аккаунт. Сессия создаётся один раз через `python login.py`.
  * BOT_CLIENT (Telethon, bot_token) — ОТПРАВЛЯЕТ сводку и принимает команды
    /start и /digest.

Запуск:  python bot.py
"""
import asyncio
import html
import json
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

import config
from summarizer import summarize

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 (на всякий случай)
    ZoneInfo = None  # type: ignore

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("parser-banking-news")
# Telethon бывает слишком болтливым на INFO — приглушаем до WARNING.
logging.getLogger("telethon").setLevel(logging.WARNING)

# Глобальные клиенты (инициализируются в main()).
USER_CLIENT: TelegramClient | None = None
BOT_CLIENT: TelegramClient | None = None

# Максимальная длина одного сообщения Telegram — 4096 символов.
# Берём с запасом под HTML-разметку.
MAX_MESSAGE_LEN = 3500


# ---------------------------------------------------------------------------
# Таймзона
# ---------------------------------------------------------------------------
def get_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(config.TIMEZONE)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось загрузить таймзону %s, использую UTC.", config.TIMEZONE)
    # Фолбэк: фиксированный UTC+3 (Москва), чтобы бот работал даже без tzdata.
    from datetime import timezone
    return timezone(timedelta(hours=3))


# ---------------------------------------------------------------------------
# Подписчики
# ---------------------------------------------------------------------------
def load_subscribers() -> list[int]:
    path = config.SUBSCRIBERS_FILE
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [int(x) for x in data]
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось прочитать %s: %s", path, exc)
    return []


def save_subscribers(subs: list[int]) -> None:
    try:
        tmp = config.SUBSCRIBERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(set(subs)), f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.SUBSCRIBERS_FILE)
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось сохранить подписчиков: %s", exc)


def add_subscriber(chat_id: int) -> bool:
    """Добавляет подписчика. Возвращает True, если это новый подписчик."""
    subs = load_subscribers()
    if chat_id in subs:
        return False
    subs.append(chat_id)
    save_subscribers(subs)
    return True


# ---------------------------------------------------------------------------
# Сбор постов
# ---------------------------------------------------------------------------
def _group_messages(raw_messages: list) -> list[dict]:
    """
    Сворачивает альбомы (несколько медиа = один пост) в одну запись и
    возвращает список {'id', 'text'} в хронологическом порядке.
    На вход приходят сообщения от новых к старым (как отдаёт iter_messages).
    """
    groups: dict[int, dict] = {}
    singles: list[dict] = []

    for msg in raw_messages:
        text = (getattr(msg, "message", None) or "").strip()
        gid = getattr(msg, "grouped_id", None)
        if gid:
            g = groups.get(gid)
            if g is None:
                groups[gid] = {"id": msg.id, "text": text}
            else:
                g["id"] = min(g["id"], msg.id)
                if text and not g["text"]:
                    g["text"] = text
        else:
            singles.append({"id": msg.id, "text": text})

    entries = singles + list(groups.values())
    entries.sort(key=lambda e: e["id"])  # хронологический порядок
    return entries


async def collect_all_posts(user_client: TelegramClient):
    """
    Возвращает (день_начала, [(заголовок_канала, username, [записи])]).
    Собирает посты за ПРОШЕДШИЙ календарный день в выбранной таймзоне.
    """
    tz = get_tz()
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    log.info(
        "Собираю посты за %s (с %s по %s).",
        yesterday_start.strftime("%d.%m.%Y"),
        yesterday_start.strftime("%H:%M"),
        today_start.strftime("%H:%M"),
    )

    results = []
    for username in config.CHANNELS:
        try:
            entity = await user_client.get_entity(username)
        except FloodWaitError as fw:
            log.warning("FloodWait %s сек на @%s, жду...", fw.seconds, username)
            await asyncio.sleep(fw.seconds + 1)
            try:
                entity = await user_client.get_entity(username)
            except Exception as exc:  # noqa: BLE001
                log.warning("Пропускаю @%s (нет доступа): %s", username, exc)
                continue
        except Exception as exc:  # noqa: BLE001
            log.warning("Пропускаю @%s (нет доступа): %s", username, exc)
            continue

        title = getattr(entity, "title", username) or username
        raw = []
        try:
            async for msg in user_client.iter_messages(
                entity, limit=config.MAX_MESSAGES_PER_CHANNEL
            ):
                if msg.date is None:
                    continue
                d = msg.date.astimezone(tz)
                if d >= today_start:
                    continue  # сегодняшние — ещё рано
                if d < yesterday_start:
                    break  # дошли до позавчера — дальше не нужно
                raw.append(msg)
        except FloodWaitError as fw:
            log.warning("FloodWait %s сек при чтении @%s, жду...", fw.seconds, username)
            await asyncio.sleep(fw.seconds + 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ошибка чтения @%s: %s", username, exc)

        entries = _group_messages(raw)
        log.info("@%s: найдено постов за день — %d", username, len(entries))
        if entries:
            results.append((title, username, entries))

    return yesterday_start, results


# ---------------------------------------------------------------------------
# Формирование сводки
# ---------------------------------------------------------------------------
def build_digest_messages(date_label: str, results: list) -> list[str]:
    """Строит список готовых к отправке сообщений (с учётом лимита длины)."""
    total_posts = sum(len(entries) for _, _, entries in results)
    header = (
        f"📰 <b>Сводка новостей за {date_label}</b>\n"
        f"Всего постов: {total_posts}\n\n"
    )

    if total_posts == 0:
        return [
            f"📰 <b>Сводка новостей за {date_label}</b>\n\n"
            f"За прошедший день новых постов в каналах не найдено."
        ]

    blocks: list[str] = []
    for title, username, entries in results:
        for e in entries:
            link = f"https://t.me/{username}/{e['id']}"
            summary = summarize(
                e["text"],
                max_sentences=config.SUMMARY_MAX_SENTENCES,
                max_chars=config.SUMMARY_MAX_CHARS,
            )
            if summary:
                body = html.escape(summary)
            else:
                body = "<i>[пост без текста: фото / видео / файл]</i>"
            block = (
                f"📌 <b>{html.escape(title)}</b>\n"
                f"{body}\n"
                f"🔗 {link}\n"
            )
            blocks.append(block)

    # Разбиваем на сообщения по лимиту длины, не разрывая отдельные посты.
    messages: list[str] = []
    current = header
    for block in blocks:
        if len(current) + len(block) + 1 > MAX_MESSAGE_LEN and current.strip():
            messages.append(current.rstrip())
            current = ""
        current += block + "\n"
    if current.strip():
        messages.append(current.rstrip())

    return messages


# ---------------------------------------------------------------------------
# Отправка
# ---------------------------------------------------------------------------
async def _safe_send(bot_client: TelegramClient, chat_id: int, text: str) -> bool:
    for attempt in range(3):
        try:
            await bot_client.send_message(
                chat_id, text, parse_mode="html", link_preview=False
            )
            return True
        except FloodWaitError as fw:
            log.warning("FloodWait %s сек при отправке в %s, жду...", fw.seconds, chat_id)
            await asyncio.sleep(fw.seconds + 1)
        except Exception as exc:  # noqa: BLE001
            log.error("Ошибка отправки в %s (попытка %d): %s", chat_id, attempt + 1, exc)
            # Возможная проблема HTML-разметки — пробуем отправить как обычный текст.
            try:
                await bot_client.send_message(chat_id, text, link_preview=False)
                return True
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2)
    log.error("Не удалось доставить сообщение в %s после 3 попыток.", chat_id)
    return False


async def run_and_send(targets: list[int] | None = None) -> None:
    """Собирает посты и рассылает сводку. targets=None -> всем подписчикам."""
    if USER_CLIENT is None or BOT_CLIENT is None:
        log.error("Клиенты не инициализированы.")
        return

    subscribers = targets if targets is not None else load_subscribers()
    if not subscribers:
        log.warning("Нет подписчиков — сводка не отправлена. Нажмите /start в боте.")
        return

    try:
        date_start, results = await collect_all_posts(USER_CLIENT)
    except Exception as exc:  # noqa: BLE001
        log.exception("Критическая ошибка при сборе постов: %s", exc)
        return

    messages = build_digest_messages(date_start.strftime("%d.%m.%Y"), results)
    log.info("Сводка готова: %d сообщений, %d получателей.", len(messages), len(subscribers))

    for chat_id in subscribers:
        for msg in messages:
            await _safe_send(BOT_CLIENT, chat_id, msg)
            await asyncio.sleep(0.4)  # бережём лимиты Telegram


# ---------------------------------------------------------------------------
# Обработчики команд бота
# ---------------------------------------------------------------------------
async def on_start(event) -> None:
    chat_id = event.chat_id
    is_new = add_subscriber(chat_id)
    tz_name = config.TIMEZONE
    when = f"{config.SEND_HOUR:02d}:{config.SEND_MINUTE:02d}"
    if is_new:
        text = (
            "✅ Вы подписаны на ежедневную сводку новостей.\n\n"
            f"Каждый день в <b>{when}</b> ({tz_name}) вы будете получать "
            "краткую выжимку всех постов из отслеживаемых каналов за прошедший день.\n\n"
            "Команды:\n"
            "• /digest — прислать сводку за вчера прямо сейчас\n"
            "• /stop — отписаться"
        )
    else:
        text = (
            "Вы уже подписаны 👍\n\n"
            f"Сводка приходит ежедневно в <b>{when}</b> ({tz_name}).\n"
            "• /digest — прислать сводку за вчера сейчас\n"
            "• /stop — отписаться"
        )
    await event.respond(text, parse_mode="html")
    log.info("Новый /start от chat_id=%s (новый подписчик: %s).", chat_id, is_new)


async def on_stop(event) -> None:
    chat_id = event.chat_id
    subs = load_subscribers()
    if chat_id in subs:
        subs = [s for s in subs if s != chat_id]
        save_subscribers(subs)
        await event.respond("Вы отписались от сводки. Чтобы вернуться — /start.")
    else:
        await event.respond("Вы и так не подписаны. Чтобы подписаться — /start.")


async def on_digest(event) -> None:
    await event.respond("⏳ Собираю сводку за вчера, это может занять до минуты...")
    try:
        await run_and_send(targets=[event.chat_id])
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка в /digest: %s", exc)
        await event.respond("⚠️ Произошла ошибка при сборе сводки. Попробуйте позже.")


# ---------------------------------------------------------------------------
# Планировщик
# ---------------------------------------------------------------------------
async def scheduled_job() -> None:
    log.info("=== Запуск ежедневной рассылки по расписанию ===")
    try:
        await run_and_send(targets=None)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка в задаче по расписанию: %s", exc)
    log.info("=== Рассылка завершена ===")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> None:
    global USER_CLIENT, BOT_CLIENT
    tz = get_tz()

    # 1. Пользовательский клиент (чтение каналов).
    USER_CLIENT = TelegramClient(config.USER_SESSION, config.API_ID, config.API_HASH)
    await USER_CLIENT.connect()
    if not await USER_CLIENT.is_user_authorized():
        log.error(
            "Пользовательская сессия не найдена или не авторизована.\n"
            "Сначала выполните однократный вход: python login.py"
        )
        await USER_CLIENT.disconnect()
        return
    me = await USER_CLIENT.get_me()
    log.info("Пользовательский аккаунт: %s (id=%s).", me.first_name, me.id)

    # 2. Бот-клиент (отправка + команды).
    BOT_CLIENT = TelegramClient(config.BOT_SESSION, config.API_ID, config.API_HASH)
    await BOT_CLIENT.start(bot_token=config.BOT_TOKEN)
    bot_me = await BOT_CLIENT.get_me()
    log.info("Бот запущен: @%s.", bot_me.username)

    # 3. Регистрируем обработчики команд.
    BOT_CLIENT.add_event_handler(on_start, events.NewMessage(pattern=r"^/start"))
    BOT_CLIENT.add_event_handler(on_stop, events.NewMessage(pattern=r"^/stop"))
    BOT_CLIENT.add_event_handler(on_digest, events.NewMessage(pattern=r"^/digest"))

    # 4. Планировщик ежедневной рассылки.
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        scheduled_job,
        trigger="cron",
        hour=config.SEND_HOUR,
        minute=config.SEND_MINUTE,
        misfire_grace_time=3600,  # если запуск чуть опоздал — всё равно выполнить
        coalesce=True,
    )
    scheduler.start()

    next_run = scheduler.get_jobs()[0].next_run_time
    log.info(
        "Бот работает. Ежедневная рассылка в %02d:%02d (%s). Ближайший запуск: %s",
        config.SEND_HOUR, config.SEND_MINUTE, config.TIMEZONE, next_run,
    )
    log.info("Откройте бота в Telegram и отправьте /start, чтобы подписаться.")

    # 5. Держим процесс живым.
    await BOT_CLIENT.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановка по сигналу. До свидания!")
