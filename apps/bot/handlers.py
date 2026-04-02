import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from django.conf import settings

logger = logging.getLogger(__name__)

MINIAPP_URL = getattr(settings, "MINIAPP_URL", "https://partners.weel.uz/")


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="Ilovani ochish",
                    web_app=WebAppInfo(url=MINIAPP_URL),
                )
            ]
        ]
    )

    await update.message.reply_text(
        "Assalomu alaykum! WEEL ilovasiga xush kelibsiz.\n\n"
        "Quyidagi tugmani bosib ilovani oching:",
        reply_markup=keyboard,
    )
