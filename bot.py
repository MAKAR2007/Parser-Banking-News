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
import re
import signal
import statistics
import time as time_module
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import relevance
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
    # Через прокси к Telegram соединение нестабильно, но сбои быстрые —
    # поэтому делаем много дешёвых повторов с короткой паузой.
    attempts = max(config.API_RETRIES, 1)
    for attempt in range(attempts):
        try:
            resp = await client.post(url, json=params)
        except Exception as exc:  # noqa: BLE001
            if attempt + 1 >= attempts:
                log.warning("Сетевая ошибка в %s после %d попыток: %s", method, attempts, exc)
            await asyncio.sleep(min(1.5 * (attempt + 1), 5))
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
                config.REQUEST_RETRIES,
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


_DEDUP_WORD_RE = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)


def _dedup_tokens(text: str) -> set[str]:
    """
    Множество «стемов» (первые 5 букв значимых слов) из начала поста.
    Грубый стемминг склеивает словоформы: «бюджета/бюджетов» -> «бюдже».
    """
    return {w[:5] for w in _DEDUP_WORD_RE.findall(text.lower()[:240])}


def _is_duplicate(tokens: set[str], chosen: list[set[str]]) -> bool:
    """
    True, если новость почти совпадает с уже отобранной.
    Метрика — containment (пересечение / меньшее множество): она устойчива к
    разной длине пересказов одной и той же новости в разных каналах.
    """
    if not tokens:
        return False
    for other in chosen:
        smaller = min(len(tokens), len(other))
        if smaller == 0:
            continue
        # Для коротких текстов поднимаем порог, чтобы не склеить разные новости.
        threshold = 0.8 if smaller < 5 else 0.6
        if len(tokens & other) / smaller >= threshold:
            return True
    return False


def select_top_posts(results: list) -> tuple[list[dict], list[dict], int]:
    """
    Двухслотовый отбор для дайджеста:

    Слот A (ECON_SLOTS=5) — «Экономика»:
      Отбираем посты с высокой тематической релевантностью (банки/макро/финансы).
      Формула: охват × rel_factor² — квадрат сильно штрафует нерелевантное.
      Только посты с rel_factor > 1.0 (есть хотя бы одно профильное слово).

    Слот B (GENERAL_SLOTS=15) — «Интересное»:
      Вирусные/интересные посты для широкой аудитории. Охват в приоритете,
      жёсткий фильтр (мат/война) сохраняется, требования к теме сняты.
      Формула: охват × max(rel_factor, 0.45) — охват решает.

    Дедупликация работает сквозная: одна история не попадёт в оба слота.
    Возвращает (econ_posts, general_posts, total_found).
    """
    scored: list[dict] = []
    total_found = 0
    blocked_count = 0

    for title, channel, posts in results:
        total_found += len(posts)
        if not posts:
            continue
        views_list = [max(p["views"], 0) for p in posts]
        baseline = max(statistics.median(views_list), 1)

        for p in posts:
            rel_factor, rel_reason = relevance.score_text(p["text"])
            if rel_factor <= 0:
                blocked_count += 1
                continue  # мат / военная повестка — никогда не публикуем

            text_len = len(p["text"].strip())
            if text_len >= config.MIN_TEXT_LEN:
                text_factor = 1.0
            elif text_len > 0:
                text_factor = 0.7
            else:
                text_factor = 0.35

            relative = p["views"] / baseline
            absolute = math.log10(p["views"] + 10)
            engagement = math.sqrt(relative) * absolute

            scored.append(
                {
                    "title": title,
                    "channel": channel,
                    "text": p["text"],
                    "url": p["url"],
                    "views": p["views"],
                    "econ_score": engagement * (rel_factor ** 2) * text_factor,
                    "general_score": engagement * max(rel_factor, 0.45) * text_factor,
                    "relevance": rel_reason,
                    "rel_factor": rel_factor,
                }
            )

    log.info(
        "Оценено: %d постов, исключено %d (мат/военная повестка).",
        total_found, blocked_count,
    )

    # --- Слот A: Экономика ---
    econ_candidates = sorted(
        [x for x in scored if x["rel_factor"] > 1.0],
        key=lambda x: x["econ_score"], reverse=True,
    )
    econ_selected: list[dict] = []
    chosen_tokens: list[set[str]] = []
    per_channel_econ: dict[str, int] = {}
    for item in econ_candidates:
        if len(econ_selected) >= config.ECON_SLOTS:
            break
        tokens = _dedup_tokens(item["text"])
        if _is_duplicate(tokens, chosen_tokens):
            continue
        ch = item["channel"]
        if per_channel_econ.get(ch, 0) < config.MAX_PER_CHANNEL:
            econ_selected.append(item)
            chosen_tokens.append(tokens)
            per_channel_econ[ch] = per_channel_econ.get(ch, 0) + 1

    # --- Слот B: Интересное ---
    econ_urls = {x["url"] for x in econ_selected}
    general_candidates = sorted(
        [x for x in scored if x["url"] not in econ_urls],
        key=lambda x: x["general_score"], reverse=True,
    )
    general_selected: list[dict] = []
    per_channel_gen: dict[str, int] = {}
    for item in general_candidates:
        if len(general_selected) >= config.GENERAL_SLOTS:
            break
        tokens = _dedup_tokens(item["text"])
        if _is_duplicate(tokens, chosen_tokens):
            continue
        ch = item["channel"]
        if per_channel_gen.get(ch, 0) < config.MAX_PER_CHANNEL:
            general_selected.append(item)
            chosen_tokens.append(tokens)
            per_channel_gen[ch] = per_channel_gen.get(ch, 0) + 1

    return econ_selected, general_selected, total_found


_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _date_ru(d: datetime) -> str:
    return f"{d.day} {_MONTHS_RU[d.month - 1]} {d.year}"


def _headline_and_body(text: str) -> tuple[str, str]:
    """
    Делит выжимку на заголовок (первая фраза, до ~90 символов) и остальное.
    Заголовок выделяется жирным — сводку можно сканировать глазами за секунды.
    """
    summary = summarize(
        text,
        max_sentences=config.SUMMARY_MAX_SENTENCES,
        max_chars=config.SUMMARY_MAX_CHARS,
    )
    if not summary:
        return "", ""
    # Первая фраза — до точки/!/?/…, иначе первые ~90 символов.
    m = re.match(r"(.{10,90}?[.!?…])\s+(.+)", summary, flags=re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if len(summary) <= 100:
        return summary, ""
    cut = summary[:90]
    sp = cut.rfind(" ")
    if sp > 40:
        return cut[:sp].rstrip() + "…", summary[sp:].strip()
    return summary, ""


def build_digest_messages(
    date_start: datetime,
    econ_posts: list[dict],
    general_posts: list[dict],
    total_found: int,
) -> list[str]:
    """Собирает готовые к отправке сообщения (HTML, с учётом лимита длины)."""
    date_label = _date_ru(date_start)
    total_selected = len(econ_posts) + len(general_posts)

    if not econ_posts and not general_posts:
        return [
            "🏦 <b>Утренняя сводка</b>\n"
            f"<i>{date_label}</i>\n\n"
            "За прошедший день релевантных новостей не нашлось — "
            "такое бывает в тихие дни. Завтра сводка придёт как обычно. ✨"
        ]

    header = (
        "🏦 <b>Утренняя сводка · главное за день</b>\n"
        f"<i>{date_label} · {total_selected} новостей из {total_found} постов</i>\n"
        f"{'─' * 22}\n\n"
    )

    def _post_block(i: int, item: dict) -> str:
        headline, body = _headline_and_body(item["text"])
        if not headline:
            headline, body = "Пост с фото/видео без текста", ""
        views_part = f" · 👁 {_human_views(item['views'])}" if item["views"] else ""
        lines = [f"<b>{i}. {html.escape(headline)}</b>"]
        if body:
            lines.append(html.escape(body))
        lines.append(
            f"<a href=\"{item['url']}\">{html.escape(item['title'])}</a>{views_part}"
        )
        return "\n".join(lines) + "\n"

    blocks: list[str] = []

    if econ_posts:
        blocks.append("💼 <b>Экономика</b>\n")
        for i, item in enumerate(econ_posts, 1):
            blocks.append(_post_block(i, item))

    if general_posts:
        blocks.append("\n📱 <b>Интересное</b>\n")
        offset = len(econ_posts)
        for i, item in enumerate(general_posts, offset + 1):
            blocks.append(_post_block(i, item))

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
    econ_posts, general_posts, total_found = select_top_posts(results)
    messages = build_digest_messages(date_start, econ_posts, general_posts, total_found)
    log.info(
        "Сводка: экономика=%d интересное=%d итого=%d из %d постов, %d сообщений, %d получателей.",
        len(econ_posts), len(general_posts), len(econ_posts) + len(general_posts),
        total_found, len(messages), len(subscribers),
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

    help_text = (
        "🏦 <b>Утренняя сводка — как это работает</b>\n\n"
        f"Каждый день в <b>{when} МСК</b> я присылаю дайджест из двух частей:\n"
        f"💼 <b>{config.ECON_SLOTS} новостей про экономику</b> — важные и понятные всем\n"
        f"📱 <b>{config.GENERAL_SLOTS} интересных постов</b> — вирусное из деловых каналов\n\n"
        "Источники — {len_ch} деловых Telegram-каналов. Мат, военная повестка "
        "и реклама отфильтрованы автоматически.\n\n"
        "<b>Команды</b>\n"
        "/digest — сводка за вчера прямо сейчас\n"
        "/status — подписка, время рассылки, каналы\n"
        "/stop — отписаться от рассылки\n"
        "/help — это сообщение"
    ).replace("{len_ch}", str(len(config.CHANNELS)))

    if cmd == "/start":
        is_new = add_subscriber(chat_id)
        if is_new:
            reply = (
                "👋 Добро пожаловать!\n\n"
                f"Вы подписаны: каждый день в <b>{when} МСК</b> сюда будет "
                f"приходить дайджест — {config.ECON_SLOTS} важных экономических "
                f"новостей и {config.GENERAL_SLOTS} интересных постов.\n\n"
                "Хотите посмотреть, как это выглядит? Нажмите /digest — "
                "пришлю сводку за вчера прямо сейчас. 👇"
            )
        else:
            reply = (
                "Вы уже подписаны 👍\n\n"
                f"Сводка приходит ежедневно в <b>{when} МСК</b>.\n"
                "/digest — сводка за вчера сейчас · /help — все команды"
            )
        await send_message(client, chat_id, reply)
        log.info("/start от chat_id=%s (новый: %s)", chat_id, is_new)

    elif cmd == "/help":
        await send_message(client, chat_id, help_text)

    elif cmd == "/status":
        subs = load_subscribers()
        subscribed = chat_id in subs
        reply = (
            "📊 <b>Статус</b>\n\n"
            f"Подписка: {'✅ активна' if subscribed else '❌ нет (включить — /start)'}\n"
            f"Рассылка: ежедневно в <b>{when} МСК</b>\n"
            f"Новостей в сводке: {config.ECON_SLOTS} экономика + {config.GENERAL_SLOTS} интересное\n"
            f"Источников: {len(config.CHANNELS)} каналов\n"
            f"Подписчиков у бота: {len(subs)}"
        )
        await send_message(client, chat_id, reply)

    elif cmd == "/stop":
        if remove_subscriber(chat_id):
            await send_message(
                client, chat_id,
                "Вы отписались от сводки. 😢\nПередумаете — просто нажмите /start.",
            )
        else:
            await send_message(client, chat_id, "Вы и так не подписаны. Подписаться — /start.")

    elif cmd == "/digest":
        # Игнорируем команды, накопившиеся пока бот не работал.
        if message.get("date", 0) < STARTED_AT:
            return
        await send_message(
            client, chat_id,
            "⏳ Собираю и ранжирую новости за вчера — обычно это занимает меньше минуты…",
        )
        try:
            await run_and_send(client, targets=[chat_id])
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка в /digest: %s", exc)
            await send_message(
                client, chat_id,
                "⚠️ Не получилось собрать сводку — попробуйте ещё раз через пару минут.",
            )

    elif cmd.startswith("/"):
        await send_message(
            client, chat_id,
            "Не знаю такой команды 🤔\n/help — список всех команд.",
        )

    else:
        # Обычный текст — мягко подсказываем, но только в личке (в группах молчим).
        if chat.get("type") == "private":
            await send_message(
                client, chat_id,
                "Я присылаю утреннюю сводку новостей и понимаю команды:\n"
                "/digest — сводка за вчера · /help — подробнее",
            )


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
    client_kwargs = {"timeout": timeout}
    if config.PROXY_URL:
        client_kwargs["proxy"] = config.PROXY_URL
        log.info("Использую прокси для всего трафика: %s", config.PROXY_URL)
    async with httpx.AsyncClient(**client_kwargs) as client:
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

        # Регистрируем меню команд в клиенте Telegram (кнопка «Меню» у поля ввода).
        await tg_call(
            client,
            "setMyCommands",
            commands=[
                {"command": "digest", "description": "📰 Сводка за вчера прямо сейчас"},
                {"command": "status", "description": "📊 Подписка и расписание"},
                {"command": "help", "description": "ℹ️ Как работает бот"},
                {"command": "stop", "description": "🔕 Отписаться от рассылки"},
            ],
        )

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
