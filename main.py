import asyncio
import hashlib
import hmac
import json

import aiohttp
from aiogram.types import BotCommand, BotCommandScopeDefault
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

from config_data import config
from handlers import routers
from loader import bot, dp
from middlewares.logging_middleware import LoggingMiddleware


LOCAL_ENV = config.LOCAL_ENV
BASE_URL = config.BASE_URL

TELEGRAM_WEBHOOK_PATH = "/webhook_"
TWITCH_WEBHOOK_PATH = "/twitch/eventsub"

PORT = 5014
HOST = "0.0.0.0"

TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
TWITCH_CLIENT_ID = config.TWITCH_CLIENT_ID
TWITCH_CLIENT_SECRET = config.TWITCH_CLIENT_SECRET
TWITCH_EVENTSUB_SECRET = config.TWITCH_EVENTSUB_SECRET

TWITCH_CALLBACK_URL = (
    f"{BASE_URL}{TWITCH_WEBHOOK_PATH}"
)


# Укажи логины стримеров из адресов twitch.tv/login
TRACKED_STREAMERS = [
    "thereisnofuture",
    "toooschi",
    "sisuka7",
    "elgris",
    "wirtual",
    "lirik",
    "honeymad",
    "c_a_k_e",
]


async def set_commands() -> None:
    commands = [
        BotCommand(
            command=command,
            description=description,
        )
        for command, description in config.DEFAULT_COMMANDS
    ]

    await bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault(),
    )


async def get_twitch_app_token(
    session: aiohttp.ClientSession,
) -> str:
    async with session.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        data = await response.json()

        if response.status != 200:
            raise RuntimeError(
                "Не удалось получить Twitch App Access Token: "
                f"{response.status} {data}"
            )

        return data["access_token"]


async def get_twitch_users(
    session: aiohttp.ClientSession,
    access_token: str,
) -> list[dict]:
    params = [
        ("login", login)
        for login in TRACKED_STREAMERS
    ]

    async with session.get(
        "https://api.twitch.tv/helix/users",
        headers={
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {access_token}",
        },
        params=params,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        data = await response.json()

        if response.status != 200:
            raise RuntimeError(
                "Не удалось получить Twitch-пользователей: "
                f"{response.status} {data}"
            )

        return data["data"]


async def create_twitch_subscription(
    session: aiohttp.ClientSession,
    access_token: str,
    streamer: dict,
) -> None:
    async with session.post(
        "https://api.twitch.tv/helix/eventsub/subscriptions",
        headers={
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "type": "stream.online",
            "version": "1",
            "condition": {
                "broadcaster_user_id": streamer["id"],
            },
            "transport": {
                "method": "webhook",
                "callback": TWITCH_CALLBACK_URL,
                "secret": TWITCH_EVENTSUB_SECRET,
            },
        },
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        data = await response.json()

        if response.status == 202:
            print(
                "Создаётся Twitch-подписка:",
                streamer["display_name"],
            )
            return

        if response.status == 409:
            print(
                "Twitch-подписка уже существует:",
                streamer["display_name"],
            )
            return

        raise RuntimeError(
            "Не удалось создать Twitch-подписку "
            f"для {streamer['display_name']}: "
            f"{response.status} {data}"
        )


async def subscribe_to_twitch_streamers() -> None:
    if not TRACKED_STREAMERS:
        print("Список TRACKED_STREAMERS пуст.")
        return

    async with aiohttp.ClientSession() as session:
        access_token = await get_twitch_app_token(session)

        streamers = await get_twitch_users(
            session=session,
            access_token=access_token,
        )

        found_logins = {
            streamer["login"].lower()
            for streamer in streamers
        }

        missing_logins = {
            login.lower()
            for login in TRACKED_STREAMERS
        } - found_logins

        for login in sorted(missing_logins):
            print(f"Twitch-канал не найден: {login}")

        for streamer in streamers:
            await create_twitch_subscription(
                session=session,
                access_token=access_token,
                streamer=streamer,
            )


def twitch_signature_is_valid(
    request: web.Request,
    body: bytes,
) -> bool:
    message_id = request.headers.get(
        "Twitch-Eventsub-Message-Id",
        "",
    )

    timestamp = request.headers.get(
        "Twitch-Eventsub-Message-Timestamp",
        "",
    )

    received_signature = request.headers.get(
        "Twitch-Eventsub-Message-Signature",
        "",
    )

    signed_message = (
        message_id.encode("utf-8")
        + timestamp.encode("utf-8")
        + body
    )

    expected_signature = (
        "sha256="
        + hmac.new(
            TWITCH_EVENTSUB_SECRET.encode("utf-8"),
            signed_message,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(
        expected_signature,
        received_signature,
    )


async def get_stream_details(
    streamer_id: str,
) -> dict | None:
    async with aiohttp.ClientSession() as session:
        access_token = await get_twitch_app_token(session)

        async with session.get(
            "https://api.twitch.tv/helix/streams",
            headers={
                "Client-Id": TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {access_token}",
            },
            params={
                "user_id": streamer_id,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            data = await response.json()

            if response.status != 200:
                print(
                    "Не удалось получить данные стрима:",
                    response.status,
                    data,
                )
                return None

            streams = data["data"]

            if not streams:
                return None

            return streams[0]


async def send_twitch_notification(
    event: dict,
) -> None:
    streamer_id = event["broadcaster_user_id"]
    streamer_login = event["broadcaster_user_login"]
    streamer_name = event["broadcaster_user_name"]

    # Twitch может прислать stream.online немного раньше,
    # чем данные появятся в GET /streams.
    await asyncio.sleep(3)

    stream = await get_stream_details(streamer_id)

    if stream:
        game_name = (
            stream["game_name"]
            or "Без категории"
        )
        title = stream["title"]

        text = (
            f"🔴 <b>{streamer_name} начал трансляцию</b>\n\n"
            f"🎮 {game_name}\n"
            f"📺 {title}\n\n"
            f"https://twitch.tv/{streamer_login}"
        )
    else:
        text = (
            f"🔴 <b>{streamer_name} начал трансляцию</b>\n\n"
            f"https://twitch.tv/{streamer_login}"
        )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=False,
    )


async def twitch_eventsub_handler(
    request: web.Request,
) -> web.Response:
    body = await request.read()

    if not twitch_signature_is_valid(request, body):
        return web.Response(
            status=403,
            text="Invalid Twitch signature",
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(
            status=400,
            text="Invalid JSON",
        )

    message_type = request.headers.get(
        "Twitch-Eventsub-Message-Type",
        "",
    )

    # Twitch проверяет, что callback действительно принадлежит тебе.
    if message_type == "webhook_callback_verification":
        return web.Response(
            text=data["challenge"],
            content_type="text/plain",
        )

    if message_type == "notification":
        subscription_type = data["subscription"]["type"]

        if subscription_type == "stream.online":
            asyncio.create_task(
                send_twitch_notification(data["event"])
            )

        return web.Response(status=204)

    if message_type == "revocation":
        print(
            "Twitch отозвал EventSub-подписку:",
            data,
        )
        return web.Response(status=204)

    return web.Response(status=204)


async def on_startup() -> None:
    await set_commands()

    await bot.set_webhook(
        f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="Бот запущен на вебхуках!",
    )

    try:
        await subscribe_to_twitch_streamers()
    except Exception as error:
        print(
            "Ошибка при создании Twitch-подписок:",
            error,
        )

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                "Не удалось создать Twitch-подписки:\n"
                f"{error}"
            ),
        )


async def on_shutdown() -> None:
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="Бот остановлен!",
        )
    finally:
        await bot.delete_webhook(
            drop_pending_updates=True
        )
        await bot.session.close()


def register_dispatcher() -> None:
    for router in routers:
        dp.include_router(router)

    dp.message.middleware(
        LoggingMiddleware()
    )
    dp.callback_query.middleware(
        LoggingMiddleware()
    )


def main_webhook() -> None:
    register_dispatcher()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    telegram_webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )

    telegram_webhook_handler.register(
        app,
        path=TELEGRAM_WEBHOOK_PATH,
    )

    app.router.add_post(
        TWITCH_WEBHOOK_PATH,
        twitch_eventsub_handler,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    web.run_app(
        app,
        host=HOST,
        port=PORT,
    )


async def main_polling() -> None:
    register_dispatcher()

    await set_commands()
    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    if LOCAL_ENV == "local":
        asyncio.run(main_polling())
    else:
        main_webhook()