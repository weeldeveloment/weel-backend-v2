import asyncio
import hashlib
import logging

from telegram import Bot, MenuButtonWebApp, WebAppInfo
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler
from telegram.request import HTTPXRequest

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
    request = HTTPXRequest(connect_timeout=10.0, read_timeout=15.0, write_timeout=15.0, pool_timeout=10.0)
    app = Application.builder().token(token).request(request).build()
    app.add_handler(CommandHandler("start", start_handler))
    return app


def _get_miniapp_url() -> str:
    return getattr(settings, "MINIAPP_URL", "https://partners.weel.uz/")


async def _call_with_retry(coro_factory, *, attempts: int = 4, base_delay: float = 1.5, label: str = "telegram"):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt >= attempts:
                logger.error(
                    "%s call failed after %s attempts: %s", label, attempts, exc
                )
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s call failed (attempt %s/%s): %s. Retrying in %.1fs",
                label, attempt, attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc


async def set_webhook(base_url: str):
    token = settings.BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN_APP is not set, skipping webhook setup.")
        return

    secret = get_webhook_secret()
    webhook_url = f"{base_url.rstrip('/')}/api/bot/webhook/{secret}/"
    miniapp_url = _get_miniapp_url()

    request = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=15.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )

    async def _do_setup():
        bot = Bot(token=token, request=request)
        async with bot:
            await bot.set_webhook(url=webhook_url, allowed_updates=["message"])
            logger.info("Telegram webhook set to %s", webhook_url)

            menu_button = MenuButtonWebApp(
                text="Ilovani ochish",
                web_app=WebAppInfo(url=miniapp_url),
            )
            await bot.set_chat_menu_button(menu_button=menu_button)
            logger.info("Telegram menu button set to open: %s", miniapp_url)

    await _call_with_retry(_do_setup, label="main_bot.set_webhook")
