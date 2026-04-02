import hashlib
import logging

from telegram import Bot, MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, CommandHandler

from django.conf import settings

from .handlers import start_handler

logger = logging.getLogger(__name__)


def get_webhook_secret() -> str:
    token = settings.BOT_TOKEN or ""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def build_application() -> Application:
    token = settings.BOT_TOKEN
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN_APP is not configured.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_handler))
    return app


def _get_miniapp_url() -> str:
    return getattr(settings, "MINIAPP_URL", "https://partners.weel.uz/")


async def set_webhook(base_url: str):
    token = settings.BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN_APP is not set, skipping webhook setup.")
        return

    secret = get_webhook_secret()
    webhook_url = f"{base_url.rstrip('/')}/api/bot/webhook/{secret}/"
    miniapp_url = _get_miniapp_url()

    bot = Bot(token=token)
    async with bot:
        await bot.set_webhook(url=webhook_url, allowed_updates=["message"])
        logger.info("Telegram webhook set to %s", webhook_url)

        # Chapdagi tugma: foydalanuvchi bosganda Web App ochiladi
        menu_button = MenuButtonWebApp(
            text="Ilovani ochish",
            web_app=WebAppInfo(url=miniapp_url),
        )
        await bot.set_chat_menu_button(menu_button=menu_button)
        logger.info("Telegram menu button set to open: %s", miniapp_url)
