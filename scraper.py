"""
Сбор постов из публичной веб-версии Telegram-каналов (t.me/s/<канал>).

Не требует api_id/api_hash и вообще никакой авторизации — работает с тем же
HTML, который Telegram отдаёт в браузере для публичных каналов.
"""
import asyncio
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("parser-banking-news.scraper")


def _parse_views(text: str) -> int:
    """'21.9K' -> 21900, '1.5M' -> 1500000, '532' -> 532."""
    if not text:
        return 0
    t = text.strip().upper().replace(",", ".")
    mult = 1
    if t.endswith("K"):
        mult, t = 1000, t[:-1]
    elif t.endswith("M"):
        mult, t = 1_000_000, t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        return 0

# Реалистичный User-Agent, чтобы Telegram отдавал полноценную веб-версию.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.9",
}


def _parse_posts(soup: BeautifulSoup, channel: str) -> list[dict]:
    """Достаёт из HTML список постов: {'id', 'date'(aware), 'text', 'url'}."""
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
            text = text_el.get_text(separator="\n").strip()
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
        try:
            resp = await client.get(
                url,
                params=params,
                headers=HEADERS,
                follow_redirects=True,
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("@%s: ошибка запроса: %s", channel, exc)
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
