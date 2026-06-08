"""
Одноразовый интерактивный вход в пользовательский аккаунт Telegram.

Зачем это нужно: бот, созданный через @BotFather, НЕ МОЖЕТ читать историю
чужих каналов (он не админ в них). Чтобы собирать посты из публичных каналов,
используется обычный пользовательский аккаунт через MTProto (Telethon).

Запускается один раз вручную:
    python login.py

Скрипт спросит номер телефона и код из Telegram (и пароль 2FA, если включён),
после чего создаст файл сессии user_session.session. Дальше bot.py будет
работать полностью автоматически, без повторного ввода.
"""
import asyncio

from telethon import TelegramClient

import config


async def main() -> None:
    client = TelegramClient(config.USER_SESSION, config.API_ID, config.API_HASH)
    # start() сам спросит телефон, код и пароль 2FA при первом запуске.
    await client.start()
    me = await client.get_me()
    print("\n[OK] Вход выполнен.")
    print(f"     Аккаунт: {me.first_name or ''} (@{me.username or 'без username'}, id={me.id})")
    print(f"     Файл сессии: {config.USER_SESSION}.session")
    print("\nТеперь проверим доступ к каналам из списка...\n")

    ok, fail = 0, 0
    for username in config.CHANNELS:
        try:
            entity = await client.get_entity(username)
            title = getattr(entity, "title", username)
            print(f"  [+] @{username:<22} -> {title}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] @{username:<22} -> НЕТ ДОСТУПА: {exc}")
            fail += 1

    print(f"\nИтог: доступно {ok} из {ok + fail} каналов.")
    if fail:
        print(
            "Каналы с пометкой [!] недоступны (приватные/не существуют/опечатка). "
            "Бот их просто пропустит — это не приведёт к ошибке."
        )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
