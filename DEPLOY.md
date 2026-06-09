# Развёртывание на сервере (Ubuntu) с обходом блокировки Telegram

На некоторых хостингах (типично для РФ) прямой доступ к Telegram (`t.me` и
`api.telegram.org`) заблокирован — пакеты к IP Telegram просто отбрасываются.
Бот в таком случае не сможет ни собирать посты, ни отправлять сводку.

Решение, которое реально работает и не требует внешнего VPN-эндпоинта, —
**Cloudflare WARP в режиме SOCKS5-прокси** на самом сервере. Трафик бота уходит
к Telegram через сеть Cloudflare. Затрагивается только бот (через `PROXY_URL`),
остальные сервисы на сервере не меняются.

## 1. Установка Cloudflare WARP
```bash
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] \
https://pkg.cloudflareclient.com/ $(. /etc/os-release; echo $VERSION_CODENAME) main" \
  > /etc/apt/sources.list.d/cloudflare-client.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflare-warp
```

## 2. Регистрация и режим прокси
```bash
warp-cli --accept-tos registration new
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port 40000
warp-cli --accept-tos connect
systemctl enable warp-svc          # автозапуск WARP при загрузке
```

## 3. Протокол MASQUE (важно для стабильности)
На хостингах, где режут WireGuard/UDP, WARP по умолчанию работает нестабильно.
Переключение на **MASQUE** (поверх QUIC/443) кардинально стабилизирует канал:
```bash
warp-cli --accept-tos tunnel protocol set MASQUE
warp-cli --accept-tos disconnect && sleep 3 && warp-cli --accept-tos connect
```
Проверка:
```bash
curl -s --socks5-hostname 127.0.0.1:40000 -o /dev/null -w "%{http_code}\n" https://t.me/s/banksta
curl -s --socks5-hostname 127.0.0.1:40000 -o /dev/null -w "%{http_code}\n" https://api.telegram.org/
```

## 4. Подключение бота к прокси
В `.env` бота:
```
PROXY_URL=socks5h://127.0.0.1:40000
```
> ⚠️ Именно `socks5h://` (а не `socks5://`) — буква `h` означает разрешение DNS
> **на стороне прокси**. Локальный DNS сервера для Telegram часто отравлен и
> возвращает нерабочий адрес (нередко IPv6), из-за чего `socks5://` падает с
> «Host unreachable». Удалённый DNS через WARP отдаёт корректный IP.

Бот спроектирован под нестабильный канал: сбои через прокси возвращаются
мгновенно, поэтому он делает много дешёвых повторов
(`REQUEST_RETRIES=12`, `API_RETRIES=8`), что надёжно «перекрывает» плохие окна —
полный сбор всех 15 каналов проходит даже в неблагоприятный момент.

## 5. systemd-сервис бота
Файл `/etc/systemd/system/parser_banking_news.service`:
```ini
[Unit]
Description=Parser Banking News Telegram Bot
Wants=warp-svc.service network-online.target
After=warp-svc.service network-online.target

[Service]
WorkingDirectory=/opt/parser_banking_news
ExecStart=/opt/parser_banking_news/venv/bin/python -u bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload
systemctl enable --now parser_banking_news
```

## Полезные команды
```bash
systemctl status parser_banking_news
journalctl -u parser_banking_news -f          # логи бота
warp-cli --accept-tos status                  # состояние WARP
systemctl restart parser_banking_news
```
