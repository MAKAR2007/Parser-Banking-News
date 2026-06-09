"""
Главный модуль бота «Parser Banking News».

Что делает:
  1. Раз в сутки (по умолчанию 08:00 по Москве) собирает все посты за прошедший
     календарный день из публичной веб-версии заданных Telegram-каналов.
  2. Делает короткую выжимку (саммари) текста каждого поста — локально, без
     внешних/платных API.
  3. Рассылает всем подписчикам сводку в формате «сокращённый текст + ссылка».

Особенность: нужен ТОЛЬКО токен бота от @BotFather. Никаких api_id/api_hash —
посты читаются из t.me/s/<канал>, рассылка идёт через обычный Bot API.

Запуск:  python bot.py
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import os
import signal
import statistics
import time as time_module
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import scraper
from summarizer import summarize

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None  # type: ignore

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("parser-banking-news")
logging.getLogger("httpx").setLevel(logging.WARNING)

API_BASE = f"https://api.telegram.org/bot{config.BOT_TOKEN}"

# Лимит длины сообщения Telegram — 4096 символов. Берём с запасом под разметку.
MAX_MESSAGE_LEN = 3500

# Момент старта процесса (UNIX). Команды /digest из «прошлого» (накопившиеся,
# пока бот лежал) не выполняем, чтобы не было неожиданных рассылок.
STARTED_AT = time_module.time()


# ---------------------------------------------------------------------------
# Таймзона
# ---------------------------------------------------------------------------
def get_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(config.TIMEZONE)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось загрузить таймзону %s, использую UTC+3.", config.TIMEZONE)
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
    subs = load_subscribers()
    if chat_id in subs:
        return False
    subs.append(chat_id)
    save_subscribers(subs)
    return True


def remove_subscriber(chat_id: int) -> bool:
    subs = load_subscribers()
    if chat_id not in subs:
        return False
    save_subscribers([s for s in subs if s != chat_id])
    return True


# ---------------------------------------------------------------------------
# Bot API helpers
# ---------------------------------------------------------------------------
async def tg_call(client: httpx.AsyncClient, method: str, **params):
    """Вызов метода Bot API с обработкой 429 (rate limit) и сетевых ошибок."""
    url = f"{API_BASE}/{method}"
    for attempt in range(4):
        try:
            resp = await client.post(url, json=params)
        except Exception as exc:  # noqa: BLE001
            log.warning("Сетевая ошибка в %s (попытка %d): %s", method, attempt + 1, exc)
            await asyncio.sleep(2 * (attempt + 1))
            continue

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            log.error("%s: не удалось разобрать ответ: %s", method, resp.text[:200])
            return None

        if data.get("ok"):
            return data.get("result")

        # Превышение лимита — Telegram говорит, сколько ждать.
        if resp.status_code == 429:
            retry_after = data.get("parameters", {}).get("retry_after", 3)
            log.warning("429 в %s, жду %s сек.", method, retry_after)
            await asyncio.sleep(retry_after + 1)
            continue

        log.error("Ошибка API %s: %s", method, data.get("description"))
        return None

    return None


async def send_message(client: httpx.AsyncClient, chat_id: int, text: str) -> bool:
    result = await tg_call(
        client,
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    if result is not None:
        return True
    # Фолбэк: возможно, проблема в HTML-разметке — пробуем без неё.
    result = await tg_call(
        client, "sendMessage", chat_id=chat_id, text=text,
        disable_web_page_preview=True,
    )
    return result is not None


# ---------------------------------------------------------------------------
# Сбор и формирование сводки
# ---------------------------------------------------------------------------
async def collect_all_posts(client: httpx.AsyncClient):
    """Возвращает (дата_начала, [(название, channel, [посты])]) за прошедший день."""
    tz = get_tz()
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    log.info("Собираю посты за %s.", yesterday_start.strftime("%d.%m.%Y"))

    results = []
    for channel in config.CHANNELS:
        try:
            title, posts = await scraper.fetch_channel_posts(
                client, channel, yesterday_start, today_start, tz,
                config.MAX_PAGES_PER_CHANNEL, config.REQUEST_DELAY,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Пропускаю @%s: %s", channel, exc)
            continue
        log.info("@%s (%s): постов за день — %d", channel, title, len(posts))
        if posts:
            results.append((title, channel, posts))

    return yesterday_start, results


def _human_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def select_top_posts(results: list, target: int) -> tuple[list[dict], int]:
    """
    Отбирает самые интересные посты дня.

    «Интересность» оцениваем по двум сигналам:
      * абсолютный охват (просмотры) — насколько пост «громкий» вообще;
      * относительный (просмотры / медиана канала) — насколько пост выстрелил
        на фоне обычного уровня своего канала.
    Относительный сигнал берём под корнем, чтобы высокочастотные каналы с
    маленькими просмотрами не вытесняли по-настоящему вирусные новости.
    Содержательные посты (с текстом) приоритетнее медиа-заглушек, и действует
    лимит постов на один канал ради разнообразия топа.

    Возвращает (отобранные_посты_в_порядке_убывания_интереса, всего_найдено).
    """
    scored: list[dict] = []
    total_found = 0

    for title, channel, posts in results:
        total_found += len(posts)
        if not posts:
            continue
        views_list = [max(p["views"], 0) for p in posts]
        # Медиана как устойчивая база канала (защищаемся от нулей).
        baseline = max(statistics.median(views_list), 1)

        for p in posts:
            text_len = len(p["text"].strip())
            if text_len >= config.MIN_TEXT_LEN:
                text_factor = 1.0
            elif text_len > 0:
                text_factor = 0.7
            else:
                text_factor = 0.35  # медиа без текста — в самом низу приоритета

            relative = p["views"] / baseline           # во сколько раз обошёл норму канала
            absolute = math.log10(p["views"] + 10)      # абсолютный охват (log)
            score = math.sqrt(relative) * absolute * text_factor

            scored.append(
                {
                    "title": title,
                    "channel": channel,
                    "text": p["text"],
                    "url": p["url"],
                    "views": p["views"],
                    "score": score,
                }
            )

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Отбор с лимитом на канал для разнообразия. Если после лимита постов
    # не хватило до target — добираем оставшимися лучшими без ограничения.
    selected: list[dict] = []
    per_channel: dict[str, int] = {}
    leftovers: list[dict] = []
    for item in scored:
        if len(selected) >= target:
            break
        ch = item["channel"]
        if per_channel.get(ch, 0) < config.MAX_PER_CHANNEL:
            selected.append(item)
            per_channel[ch] = per_channel.get(ch, 0) + 1
        else:
            leftovers.append(item)
    if len(selected) < target:
        selected.extend(leftovers[: target - len(selected)])
        selected.sort(key=lambda x: x["score"], reverse=True)

    return selected, total_found


def build_digest_messages(date_label: str, selected: list[dict], total_found: int) -> list[str]:
    """Собирает готовые к отправке сообщения (с учётом лимита длины)."""
    if not selected:
        return [
            f"📰 <b>Сводка новостей за {date_label}</b>\n\n"
            f"За прошедший день подходящих постов не найдено."
        ]

    header = (
        f"📰 <b>Топ-{len(selected)} новостей за {date_label}</b>\n"
        f"Отобрано из {total_found} постов за день.\n\n"
    )

    blocks: list[str] = []
    for i, item in enumerate(selected, 1):
        summary = summarize(
            item["text"],
            max_sentences=config.SUMMARY_MAX_SENTENCES,
            max_chars=config.SUMMARY_MAX_CHARS,
        )
        body = html.escape(summary) if summary else "<i>[пост без текста: фото / видео / файл]</i>"
        views_str = f"👁 {_human_views(item['views'])}  " if item["views"] else ""
        blocks.append(
            f"<b>{i}. {html.escape(item['title'])}</b>\n"
            f"{body}\n"
            f"{views_str}🔗 {item['url']}\n"
        )

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


async def run_and_send(client: httpx.AsyncClient, targets: list[int] | None = None) -> None:
    """Собирает посты и рассылает сводку. targets=None -> всем подписчикам."""
    subscribers = targets if targets is not None else load_subscribers()
    if not subscribers:
        log.warning("Нет подписчиков — сводка не отправлена. Нажмите /start в боте.")
        return

    date_start, results = await collect_all_posts(client)
    selected, total_found = select_top_posts(results, config.DIGEST_TARGET)
    messages = build_digest_messages(date_start.strftime("%d.%m.%Y"), selected, total_found)
    log.info(
        "Сводка готова: топ-%d из %d постов, %d сообщений, %d получателей.",
        len(selected), total_found, len(messages), len(subscribers),
    )

    for chat_id in subscribers:
        for msg in messages:
            await send_message(client, chat_id, msg)
            await asyncio.sleep(0.4)  # бережём лимиты Telegram


# ---------------------------------------------------------------------------
# Обработка команд (long polling)
# ---------------------------------------------------------------------------
async def handle_command(client: httpx.AsyncClient, message: dict) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None or not text:
        return

    cmd = text.split()[0].lower().split("@")[0]  # /start@BotName -> /start
    when = f"{config.SEND_HOUR:02d}:{config.SEND_MINUTE:02d}"

    if cmd == "/start":
        is_new = add_subscriber(chat_id)
        if is_new:
            reply = (
                "✅ Вы подписаны на ежедневную сводку новостей.\n\n"
                f"Каждый день в <b>{when}</b> ({config.TIMEZONE}) вы будете получать "
                "краткую выжимку всех постов из отслеживаемых каналов за прошедший день.\n\n"
                "Команды:\n"
                "• /digest — прислать сводку за вчера сейчас\n"
                "• /stop — отписаться"
            )
        else:
            reply = (
                "Вы уже подписаны 👍\n\n"
                f"Сводка приходит ежедневно в <b>{when}</b> ({config.TIMEZONE}).\n"
                "• /digest — прислать сводку за вчера сейчас\n"
                "• /stop — отписаться"
            )
        await send_message(client, chat_id, reply)
        log.info("/start от chat_id=%s (новый: %s)", chat_id, is_new)

    elif cmd == "/stop":
        if remove_subscriber(chat_id):
            await send_message(client, chat_id, "Вы отписались. Вернуться — /start.")
        else:
            await send_message(client, chat_id, "Вы не были подписаны. Подписаться — /start.")

    elif cmd == "/digest":
        # Игнорируем команды, накопившиеся пока бот не работал.
        if message.get("date", 0) < STARTED_AT:
            return
        await send_message(client, chat_id, "⏳ Собираю сводку за вчера, это займёт до минуты...")
        try:
            await run_and_send(client, targets=[chat_id])
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка в /digest: %s", exc)
            await send_message(client, chat_id, "⚠️ Произошла ошибка при сборе сводки.")


async def poll_updates(client: httpx.AsyncClient) -> None:
    """Бесконечный long polling getUpdates для приёма команд."""
    offset = None
    # Сбрасываем «хвост» старых апдейтов, чтобы стартовать с чистого листа по offset.
    try:
        initial = await tg_call(client, "getUpdates", timeout=0, offset=-1)
        if initial:
            offset = initial[-1]["update_id"] + 1
    except Exception:  # noqa: BLE001
        pass

    while True:
        updates = await tg_call(client, "getUpdates", timeout=25, offset=offset)
        if updates is None:
            # Ошибка API/сети (в т.ч. транзиентный 409 после перезапуска или
            # «флап» сети). Не молотим API в плотном цикле — выдерживаем паузу.
            await asyncio.sleep(3)
            continue
        if not updates:
            continue  # long-poll истёк без новых апдейтов — это норма
        for upd in updates:
            offset = upd["update_id"] + 1
            message = upd.get("message") or upd.get("channel_post")
            if message:
                try:
                    await handle_command(client, message)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Ошибка обработки апдейта: %s", exc)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
async def main() -> None:
    tz = get_tz()

    # Корректное завершение по сигналу (важно под systemd, чтобы перезапуски
    # не оставляли «висящих» long-poll и не плодили конфликтов getUpdates).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    # Короткий таймаут на установку соединения (быстро отбрасываем «мёртвые»
    # IP при флапе сети), но длинный на чтение — для long-poll getUpdates(25с).
    timeout = httpx.Timeout(35.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Сеть до Telegram может «флапать» — ждём успешного подключения,
        # а не падаем сразу. Это надёжнее под systemd.
        me = None
        while not stop.is_set():
            me = await tg_call(client, "getMe")
            if me:
                break
            log.warning(
                "Telegram недоступен (проверьте сеть/BOT_TOKEN). Повтор через 10 с..."
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        if not me:
            return  # получен сигнал остановки до подключения

        log.info("Бот запущен: @%s (id=%s).", me.get("username"), me.get("id"))

        scheduler = AsyncIOScheduler(timezone=tz)
        scheduler.add_job(
            scheduled_job,
            trigger="cron",
            hour=config.SEND_HOUR,
            minute=config.SEND_MINUTE,
            args=[client],
            misfire_grace_time=3600,
            coalesce=True,
        )
        scheduler.start()

        next_run = scheduler.get_jobs()[0].next_run_time
        log.info(
            "Ежедневная рассылка в %02d:%02d (%s). Ближайший запуск: %s",
            config.SEND_HOUR, config.SEND_MINUTE, config.TIMEZONE, next_run,
        )
        log.info("Откройте бота в Telegram и отправьте /start, чтобы подписаться.")

        poll_task = asyncio.create_task(poll_updates(client))
        await stop.wait()
        log.info("Получен сигнал остановки, завершаю работу...")
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass


async def scheduled_job(client: httpx.AsyncClient) -> None:
    log.info("=== Запуск ежедневной рассылки по расписанию ===")
    try:
        await run_and_send(client, targets=None)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка в задаче по расписанию: %s", exc)
    log.info("=== Рассылка завершена ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановка по сигналу. До свидания!")
