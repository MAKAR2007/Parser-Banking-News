"""
Сбор постов из публичной веб-версии Telegram-каналов (t.me/s/<канал>).

Не требует api_id/api_hash и вообще никакой авторизации — работает с тем же
HTML, который Telegram отдаёт в браузере для публичных каналов.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("parser-banking-news.scraper")

# Реалистичный User-Agent, чтобы Telegram отдавал полноценную веб-версию.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.9",
}


def _parse_views(raw: str) -> int:
    """Преобразует строку просмотров t.me ('12.3K', '1.2M', '532') в число."""
    if not raw:
        return 0
    s = raw.strip().upper().replace(",", ".").replace(" ", "")
    try:
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# Хвосты-подписи каналов в конце поста: «@channel», «Подписаться», эмодзи+имя
# канала («👍 Бэкдор»), призывы — для новостной выжимки это шум.
_PROMO_TAIL_RE = re.compile(
    r"(?:"
    r"\s+@[A-Za-z0-9_]{3,}\s*$"                          # @handle в конце поста
    r"|\n+\s*(?:[\W_]*\s*)?(?:подписаться|подпишись|подписывайтесь|"
    r"наш канал|читать (?:дальше|полностью)|источник|"
    r"прислать новость|предложить новость|реклама|сотрудничество)\b.*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)
# Финальная строка вида «<эмодзи> Имя Канала» (самоподпись) — срезаем, если
# это короткая строка из 1–4 слов с ведущим непечатным/эмодзи символом.
_SIGN_LINE_RE = re.compile(r"\n+\s*[^\w\s]{1,3}\s*[A-ЯЁA-Z][\w ]{1,30}\s*$")


def _clean_post_text(text: str) -> str:
    """Убирает рекламные/служебные хвосты-подписи в конце поста."""
    if not text:
        return ""
    cleaned = text
    for _ in range(3):  # хвостов может быть несколько подряд
        new = _PROMO_TAIL_RE.sub("", cleaned)
        new = _SIGN_LINE_RE.sub("", new)
        if new == cleaned:
            break
        cleaned = new
    return cleaned.strip()


def _parse_posts(soup: BeautifulSoup, channel: str) -> list[dict]:
    """Достаёт из HTML список постов: {'id', 'date'(aware), 'text', 'url', 'views'}."""
    posts = []
    for message in soup.select(".tgme_widget_message"):
        # Пропускаем служебные сообщения (закрепления, «pinned a photo» и т.п.).
        classes = message.get("class") or []
        if "service_message" in classes:
            continue

        data_post = message.get("data-post")  # формат "channel/12345"
        if not data_post or "/" not in data_post:
            continue
        try:
            msg_id = int(data_post.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue

        time_el = message.select_one("time[datetime]")
        if time_el is None or not time_el.get("datetime"):
            continue
        try:
            dt = datetime.fromisoformat(time_el["datetime"])
        except ValueError:
            continue

        text_el = message.select_one(".tgme_widget_message_text")
        if text_el is not None:
            # separator="\n" сохраняет переносы строк из <br>.
            text = _clean_post_text(text_el.get_text(separator="\n").strip())
        else:
            text = ""

        views_el = message.select_one(".tgme_widget_message_views")
        views = _parse_views(views_el.get_text()) if views_el else 0

        posts.append(
            {
                "id": msg_id,
                "date": dt,
                "text": text,
                "views": views,
                "url": f"https://t.me/{channel}/{msg_id}",
            }
        )
    return posts


async def fetch_channel_posts(
    client: httpx.AsyncClient,
    channel: str,
    start,  # aware datetime в нужной TZ — начало периода (включительно)
    end,    # aware datetime — конец периода (не включая)
    tz,
    max_pages: int,
    delay: float,
    retries: int = 3,
) -> tuple[str, list[dict]]:
    """
    Возвращает (название_канала, список_постов за период [start, end)).
    Листает веб-версию назад, пока не уйдёт за нижнюю границу периода.
    Никогда не бросает исключений наружу — при проблемах вернёт что успело собраться.
    """
    title = channel
    collected: dict[int, dict] = {}
    before = None
    first_page = True

    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}"
        params = {"before": before} if before else None

        # Повторяем запрос при сетевой ошибке — сеть до Telegram может «флапать».
        resp = None
        for attempt in range(max(retries, 1)):
            try:
                resp = await client.get(
                    url,
                    params=params,
                    headers=HEADERS,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, connect=15.0),
                )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 >= max(retries, 1):
                    log.warning("@%s: ошибка запроса после %d попыток: %r",
                                channel, max(retries, 1), exc)
                else:
                    # Сбои через прокси быстрые — короткая пауза, чтобы успеть
                    # попасть в «хорошее окно» нестабильного соединения.
                    await asyncio.sleep(1.5)
        if resp is None:
            break

        if resp.status_code != 200:
            log.warning("@%s: HTTP %s", channel, resp.status_code)
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        if first_page:
            og = soup.find("meta", attrs={"property": "og:title"})
            if og and og.get("content"):
                title = og["content"].strip() or channel
            first_page = False

        posts = _parse_posts(soup, channel)
        if not posts:
            # Нет постов в веб-версии (приватный/закрыт preview/опечатка).
            break

        for p in posts:
            d = p["date"].astimezone(tz)
            if start <= d < end:
                collected[p["id"]] = p

        oldest_date = min(p["date"].astimezone(tz) for p in posts)
        min_id = min(p["id"] for p in posts)

        # Дошли до постов старше начала периода — дальше листать незачем.
        if oldest_date < start:
            break
        # Защита от зацикливания, если пагинация не двигается.
        if before is not None and min_id >= before:
            break
        before = min_id

        await asyncio.sleep(delay)

    result = sorted(collected.values(), key=lambda p: p["id"])
    return title, result
