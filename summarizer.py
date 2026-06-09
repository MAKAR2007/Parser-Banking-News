"""
Лёгкая экстрактивная саммаризация без внешних API и без платных сервисов.
Работает на чистом Python (без интернета и ключей), поддерживает русский текст.

Идея: убираем мусор, режем текст на предложения, ранжируем предложения по
частоте значимых слов и берём самые «весомые», сохраняя исходный порядок.
Если текст и так короткий — возвращаем его как есть (с аккуратным обрезанием).
"""
from __future__ import annotations

import re

# Очень частые служебные слова, которые не должны влиять на вес предложения.
_STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже",
    "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому",
    "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти", "мой",
    "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех", "никогда",
    "можно", "при", "наконец", "два", "об", "другой", "хоть", "после", "над",
    "больше", "тот", "через", "эти", "нас", "про", "всего", "них", "какая",
    "много", "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой",
    "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им", "более",
    "всегда", "конечно", "всю", "между",
}

_URL_RE = re.compile(r"https?://\S+|t\.me/\S+|www\.\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
# Делим на предложения по .!?… и переводам строки.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")


def _truncate(text: str, max_chars: int) -> str:
    """Аккуратно обрезает строку до max_chars по границе слова, добавляя «…»."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars].rstrip()
    # Откатываемся до последнего пробела, чтобы не рвать слово посередине.
    last_space = cut.rfind(" ")
    if last_space > max_chars * 0.5:
        cut = cut[:last_space].rstrip()
    cut = cut.rstrip(".,;:—-")
    return cut + "…"


def summarize(text: str, max_sentences: int = 2, max_chars: int = 400) -> str:
    """
    Возвращает сокращённый вариант текста поста.
    Никогда не бросает исключений — при любой проблеме возвращает обрезанный текст.
    """
    if not text:
        return ""
    try:
        # 1. Чистим: убираем ссылки (они и так будут отдельной строкой) и лишние пробелы.
        cleaned = _URL_RE.sub(" ", text)
        cleaned = _WS_RE.sub(" ", cleaned).strip()
        if not cleaned:
            return ""

        if len(cleaned) <= max_chars:
            return cleaned

        # 2. Режем на предложения.
        sentences = [s.strip() for s in _SENT_SPLIT_RE.split(cleaned) if s.strip()]
        if len(sentences) <= max_sentences:
            return _truncate(cleaned, max_chars)

        # 3. Частотный словарь значимых слов.
        freq: dict[str, int] = {}
        for word in _WORD_RE.findall(cleaned.lower()):
            if len(word) <= 2 or word in _STOPWORDS:
                continue
            freq[word] = freq.get(word, 0) + 1

        if not freq:
            return _truncate(cleaned, max_chars)

        # 4. Оценка каждого предложения (нормируем на длину, чтобы не побеждали
        #    самые длинные предложения автоматически).
        scored = []
        for idx, sent in enumerate(sentences):
            words = [
                w for w in _WORD_RE.findall(sent.lower())
                if len(w) > 2 and w not in _STOPWORDS
            ]
            if not words:
                score = 0.0
            else:
                score = sum(freq.get(w, 0) for w in words) / len(words)
            # Лёгкий бонус первым предложениям (часто несут суть поста).
            if idx == 0:
                score *= 1.5
            elif idx == 1:
                score *= 1.2
            scored.append((score, idx, sent))

        # 5. Берём top-N и восстанавливаем исходный порядок.
        top = sorted(scored, key=lambda x: x[0], reverse=True)[:max_sentences]
        top.sort(key=lambda x: x[1])
        summary = " ".join(s for _, _, s in top)

        return _truncate(summary, max_chars)
    except Exception:
        # На всякий случай: в проде лучше вернуть обрезанный исходник, чем упасть.
        return _truncate(_WS_RE.sub(" ", text).strip(), max_chars)
