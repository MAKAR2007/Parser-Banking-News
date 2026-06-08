# Parser Banking News

Телеграм-бот, который **раз в сутки в 08:00 по Москве** присылает подписчикам
сводку всех постов за прошедший день из заданного списка каналов: короткая
выжимка текста каждого поста + ссылка на оригинал.

## Отслеживаемые каналы

`prbezposhady`, `blogbankir`, `karaulny_accountant`, `ecotopor`, `banksta`,
`bankrollo`, `finkrolik`, `smmrus`, `sell_me`, `lider`, `boomers_TV`,
`sale_caviar`, `whackdoor`, `trendsetter`, `concertzaal`.

Список можно менять в `config.py` или через переменную `CHANNELS` в `.env`.

---

## ⚠️ Важно прочитать перед запуском

Бот, созданный через **@BotFather**, **технически не может читать историю чужих
каналов** — Telegram отдаёт боту сообщения канала, только если бот является его
администратором. Сделать бота админом во всех этих каналах нельзя (они вам не
принадлежат).

Поэтому архитектура такая:

| Компонент | Кто | Что делает |
|-----------|-----|------------|
| Пользовательский клиент | ваш обычный аккаунт Telegram (через MTProto/Telethon) | **читает** посты из публичных каналов |
| Бот-клиент | токен от @BotFather | **отправляет** сводку и принимает команды |

Из-за этого нужны **два** набора данных:
1. Токен бота (у вас уже есть).
2. `api_id` и `api_hash` вашего аккаунта — берутся бесплатно на
   <https://my.telegram.org>.

Всё бесплатно, никаких платных сервисов и ключей OpenAI: саммаризация работает
локально на чистом Python.

---

## Установка (macOS)

В Терминале выполняйте команды по порядку.

### 1. Перейти в папку проекта
```bash
cd ~/Documents/GitHub/Parser-Banking-News
```

### 2. Создать виртуальное окружение и установить зависимости
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Получить api_id и api_hash
1. Откройте <https://my.telegram.org> и войдите под своим номером телефона.
2. Перейдите в **API development tools**.
3. Создайте приложение (любое название, например `news parser`).
4. Скопируйте **api_id** (число) и **api_hash** (строка).

### 4. Заполнить файл `.env`
В проекте уже есть готовый `.env` с вашим токеном бота. Откройте его и впишите
`API_ID` и `API_HASH`:
```bash
open -e .env
```
Минимально нужно заполнить:
```
API_ID=123456
API_HASH=ваш_api_hash
BOT_TOKEN=8882438461:AAEfIR90BlcUGW0BVey5Ub97ed4PkDz0DD8
```

### 5. Однократный вход в аккаунт (создаёт сессию для чтения каналов)
```bash
python login.py
```
Введите номер телефона в формате `+79991234567`, затем код из Telegram
(и пароль 2FA, если он включён). Скрипт проверит доступ к каждому каналу и
создаст файл `user_session.session`. Повторно это делать не нужно.

### 6. Запуск бота
```bash
python bot.py
```
Бот запустится и будет ждать 08:00 по Москве. **Откройте бота в Telegram и
отправьте `/start`**, чтобы подписаться на сводку.

---

## Команды бота
- `/start` — подписаться на ежедневную сводку.
- `/digest` — получить сводку за вчера прямо сейчас (удобно для проверки).
- `/stop` — отписаться.

---

## Как оставить бота работать постоянно (autostart на macOS)

Чтобы бот работал в фоне и сам перезапускался после перезагрузки Mac,
используйте `launchd`.

1. Создайте файл
   `~/Library/LaunchAgents/com.parserbankingnews.bot.plist` со следующим
   содержимым (замените `ВАШ_ПОЛЬЗОВАТЕЛЬ` на имя пользователя из вывода `whoami`):

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

Полезные команды:
```bash
# остановить
launchctl unload ~/Library/LaunchAgents/com.parserbankingnews.bot.plist
# посмотреть логи
tail -f ~/Documents/GitHub/Parser-Banking-News/bot.log
```

> Важно: вход (`python login.py`) нужно сделать **до** запуска через launchd,
> потому что launchd запускает процесс без интерактивного ввода.

---

## Безопасность

- Файлы `.env`, `*.session` и `subscribers.json` добавлены в `.gitignore` и
  **не попадают в репозиторий**.
- Токен бота из условия задачи виден публично — **настоятельно рекомендуется
  перевыпустить его** в @BotFather (`/revoke`) и вписать новый в `.env`.

---

## Структура проекта
```
Parser-Banking-News/
├── bot.py            # основной модуль: планировщик, сбор постов, рассылка
├── login.py          # одноразовый вход в аккаунт для чтения каналов
├── summarizer.py     # локальная саммаризация текста (без внешних API)
├── config.py         # конфигурация из .env
├── requirements.txt  # зависимости
├── .env.example      # шаблон настроек
├── .gitignore
└── README.md
```

## Как это работает внутри
1. В 08:00 (МСК) планировщик APScheduler запускает сбор постов.
2. Для каждого канала берутся сообщения с датой за прошедший календарный день
   (00:00–23:59 МСК). Альбомы из нескольких медиа считаются одним постом.
3. Текст каждого поста сокращается экстрактивным алгоритмом (ранжирование
   предложений по частоте значимых слов).
4. Формируется сводка `выжимка + ссылка`, при необходимости разбивается на
   несколько сообщений (лимит Telegram — 4096 символов), и рассылается всем,
   кто нажал `/start`.
