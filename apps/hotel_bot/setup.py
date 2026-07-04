import hashlib
import logging

from django.conf import settings

from telegram import Bot, MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from .handlers import start_handler, message_handler, callback_handler, new_handler

logger = logging.getLogger(__name__)


def get_webhook_secret() -> str:
    token = settings.HOTEL_BOT_TOKEN or ""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def build_application() -> Application:
    token = settings.HOTEL_BOT_TOKEN
    if not token:
        raise ValueError("HOTEL_BOT_TOKEN is not configured.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("new", new_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return app


async def set_webhook(base_url: str) -> None:
    token = settings.HOTEL_BOT_TOKEN
    if not token:
        logger.error("HOTEL_BOT_TOKEN is not set, skipping webhook setup.")
        return

    secret = get_webhook_secret()
    webhook_url = f"{base_url.rstrip('/')}/api/hotel-bot/webhook/{secret}/"
    pms_url = getattr(settings, "PMS_MINIAPP_URL", "https://pms.weel.uz")

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(connect_timeout=8.0, read_timeout=10.0)
    bot = Bot(token=token, request=request)
    async with bot:
        await bot.set_webhook(url=webhook_url, allowed_updates=["message", "callback_query"])
        logger.info("Hotel bot webhook set to %s", webhook_url)

        menu_button = MenuButtonWebApp(
            text="PMS ochish",
            web_app=WebAppInfo(url=pms_url),
        )
        await bot.set_chat_menu_button(menu_button=menu_button)
        logger.info("Hotel bot menu button set to %s", pms_url)
