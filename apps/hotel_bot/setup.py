import asyncio
import hashlib
import logging

from django.conf import settings

from telegram import Bot, MenuButtonWebApp, Update, WebAppInfo
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest

from .handlers import start_handler, message_handler, callback_handler, new_handler

logger = logging.getLogger(__name__)


def get_webhook_secret() -> str:
    token = settings.HOTEL_BOT_TOKEN or ""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error in hotel bot update handling", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Kechirasiz, xatolik yuz berdi. Iltimos, /start orqali qaytadan urinib ko'ring.",
            )
        except Exception:
            logger.exception("Failed to notify user about hotel bot error")


def build_application() -> Application:
    token = settings.HOTEL_BOT_TOKEN
    if not token:
        raise ValueError("HOTEL_BOT_TOKEN is not configured.")
    request = HTTPXRequest(connect_timeout=10.0, read_timeout=15.0, write_timeout=15.0, pool_timeout=10.0)
    app = Application.builder().token(token).request(request).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("new", new_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(_on_error)
    return app


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


async def set_webhook(base_url: str) -> None:
    token = settings.HOTEL_BOT_TOKEN
    if not token:
        logger.error("HOTEL_BOT_TOKEN is not set, skipping webhook setup.")
        return

    secret = get_webhook_secret()
    webhook_url = f"{base_url.rstrip('/')}/api/hotel-bot/webhook/{secret}/"
    # Falls back to the same host as the setting itself, so the two cannot
    # drift apart and quietly point the menu button somewhere dead.
    pms_url = getattr(settings, "PMS_MINIAPP_URL", None) or "https://weelrooms.uz"

    request = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=15.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )

    async def _do_setup():
        bot = Bot(token=token, request=request)
        async with bot:
            await bot.set_webhook(
                url=webhook_url,
                allowed_updates=["message", "callback_query"],
            )
            logger.info("Hotel bot webhook set to %s", webhook_url)

            menu_button = MenuButtonWebApp(
                text="PMS ochish",
                web_app=WebAppInfo(url=pms_url),
            )
            await bot.set_chat_menu_button(menu_button=menu_button)
            logger.info("Hotel bot menu button set to %s", pms_url)

    await _call_with_retry(_do_setup, label="hotel_bot.set_webhook")
