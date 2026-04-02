import asyncio
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from telegram import Bot
from telegram.error import Conflict

from bot.setup import build_application

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the Telegram bot in polling mode (local development only)"

    def handle(self, *args, **options):
        token = getattr(settings, "BOT_TOKEN", None)
        if not token:
            self.stderr.write(
                self.style.ERROR("TELEGRAM_BOT_TOKEN_APP is not configured.")
            )
            return

        self.stdout.write("Cleaning up: deleting webhook and flushing pending updates...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._clean_start(token))

        self.stdout.write(self.style.SUCCESS("Starting Telegram bot in polling mode..."))
        app = build_application()
        app.run_polling(drop_pending_updates=True)

    @staticmethod
    async def _clean_start(token: str):
        bot = Bot(token=token)
        async with bot:
            await bot.delete_webhook(drop_pending_updates=True)
            # Flush any lingering getUpdates sessions by consuming with short timeout
            try:
                await bot.get_updates(offset=-1, timeout=1)
            except Conflict:
                pass
            await asyncio.sleep(1)
