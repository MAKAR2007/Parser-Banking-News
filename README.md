# Parser Banking News

Телеграм-бот, который **раз в сутки в 08:00 по Москве** присылает подписчикам
сводку **самых интересных новостей** за прошедший день из заданного списка
каналов: для каждой — короткая выжимка текста + ссылка на оригинал.

## Что делает
- Собирает все посты за прошедший день из 15 каналов.
- **Отбирает топ-25 самых ярких** (а не все подряд): ранжирует посты по охвату
  (число просмотров) и «выстрелу» относительно обычного уровня канала, с лимитом
  постов на канал ради разнообразия. Число настраивается (`DIGEST_TARGET`).
- Делает короткую локальную выжимку каждого поста и шлёт сводку подписчикам.

## Главное преимущество этой версии
Нужен **только токен бота** от @BotFather. Никаких `api_id`/`api_hash` и входа
через my.telegram.org — посты читаются из публичной веб-версии каналов
(`t.me/s/<канал>`), а рассылка идёт через обычный Bot API. Саммаризация и отбор
работают локально, без платных сервисов и ключей.

> ⚠️ **Требование к серверу:** машина, где запущен бот, должна иметь прямой
> доступ к Telegram (`t.me` и `api.telegram.org`). Если у сервера доступ к
> Telegram заблокирован/нестабилен (типично для части РФ-хостингов), укажите
> рабочий прокси в `PROXY_URL` (поддерживаются HTTP и SOCKS5) — он будет
> использован для всего трафика бота. На macOS, как правило, всё работает без
> прокси.

## Отслеживаемые каналы
`prbezposhady`, `blogbankir`, `karaulny_accountant`, `ecotopor`, `banksta`,
`bankrollo`, `finkrolik`, `smmrus`, `sell_me`, `lider`, `boomers_TV`,
`sale_caviar`, `whackdoor`, `trendsetter`, `concertzaal`.

Список меняется в `config.py` или через переменную `CHANNELS` в `.env`.
Работает только с **публичными** каналами (у которых открыта веб-версия).

---

## Установка (macOS)

В Терминале по порядку:

### 1. Перейти в папку проекта
```bash
cd ~/Documents/GitHub/Parser-Banking-News
```

### 2. Виртуальное окружение и зависимости
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Указать токен бота
В проекте уже есть готовый `.env` с вашим токеном. Если нужно поменять:
```bash
open -e .env
```
```
BOT_TOKEN=ваш_токен_от_botfather
```

### 4. Запуск
```bash
python bot.py
```
Бот запустится и будет ждать 08:00 по Москве. **Откройте бота в Telegram и
отправьте `/start`**, чтобы подписаться.

---

## Команды бота
- `/start` — подписаться на ежедневную сводку.
- `/digest` — получить сводку за вчера прямо сейчас (удобно для проверки).
- `/stop` — отписаться.

> Совет: после первого запуска отправьте боту `/digest` — он сразу соберёт и
> пришлёт сводку за вчера, и вы убедитесь, что всё работает.

---

## Автозапуск на macOS (launchd)

Чтобы бот работал в фоне и перезапускался после перезагрузки Mac:

1. Создайте файл `~/Library/LaunchAgents/com.parserbankingnews.bot.plist`
   (замените `ВАШ_ПОЛЬЗОВАТЕЛЬ` на вывод команды `whoami`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.parserbankingnews.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Documents/GitHub/Parser-Banking-News/.venv/bin/python</string>
        <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Documents/GitHub/Parser-Banking-News/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Documents/GitHub/Parser-Banking-News</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Documents/GitHub/Parser-Banking-News/bot.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/ВАШ_ПОЛЬЗОВАТЕЛЬ/Documents/GitHub/Parser-Banking-News/bot.log</string>
</dict>
</plist>
```

2. Загрузите сервис:
```bash
launchctl load ~/Library/LaunchAgents/com.parserbankingnews.bot.plist
```

Полезное:
```bash
# остановить
launchctl unload ~/Library/LaunchAgents/com.parserbankingnews.bot.plist
# смотреть логи
tail -f ~/Documents/GitHub/Parser-Banking-News/bot.log
```

> Mac должен быть включён и не спать к 08:00, иначе рассылка сдвинется на момент
> пробуждения (предусмотрен «льготный период» misfire_grace_time = 1 час).

---

## Безопасность
- `.env`, `subscribers.json` в `.gitignore` и в репозиторий не попадают.
- Токен бота светился в чате — рекомендуется перевыпустить его в @BotFather
  (`/revoke`) и вписать новый в `.env`.

---

## Структура проекта
```
Parser-Banking-News/
├── bot.py            # Bot API, планировщик, сбор постов, рассылка, команды
├── scraper.py        # сбор постов из веб-версии каналов (t.me/s/<канал>)
├── summarizer.py     # локальная саммаризация текста (без внешних API)
├── config.py         # конфигурация из .env
├── requirements.txt  # зависимости
├── .env.example      # шаблон настроек
├── .gitignore
└── README.md
```

## Как это работает внутри
1. В 08:00 (МСК) планировщик APScheduler запускает сбор постов.
2. Для каждого канала открывается `t.me/s/<канал>`, страница парсится
   (BeautifulSoup), при необходимости листается назад до полного охвата
   прошедшего календарного дня (00:00–23:59 МСК). Запросы повторяются при
   сетевых сбоях (`REQUEST_RETRIES`).
3. **Отбор топа:** все посты ранжируются по «интересности» —
   `√(просмотры / медиана_канала) × log10(просмотры) × текстовый_фактор`.
   Берутся лучшие `DIGEST_TARGET` (по умолчанию 25) с лимитом `MAX_PER_CHANNEL`
   на канал ради разнообразия.
4. Текст каждого отобранного поста сокращается экстрактивным алгоритмом
   (ранжирование предложений по частоте значимых слов).
5. Формируется сводка `№ + канал + выжимка + просмотры + ссылка`, при
   необходимости разбивается на несколько сообщений (лимит Telegram — 4096
   символов) и рассылается всем, кто нажал `/start`.

## Настройки (`.env`)
| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `BOT_TOKEN` | — | токен бота от @BotFather (обязательно) |
| `DIGEST_TARGET` | `25` | сколько лучших новостей в сводке (20–30) |
| `MAX_PER_CHANNEL` | `5` | максимум постов от одного канала в топе |
| `MIN_TEXT_LEN` | `40` | мин. длина текста для полного приоритета |
| `SEND_HOUR` / `SEND_MINUTE` | `8` / `0` | время рассылки |
| `TIMEZONE` | `Europe/Moscow` | часовой пояс рассылки |
| `REQUEST_RETRIES` | `3` | повторы запроса страницы при сбое сети |
| `PROXY_URL` | — | прокси для всего трафика (если сервер не видит Telegram) |
| `CHANNELS` | встроенный список | список каналов через запятую |
