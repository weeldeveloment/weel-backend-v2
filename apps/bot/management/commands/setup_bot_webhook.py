import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from bot.setup import set_webhook

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Register the Telegram bot webhook with Telegram API"

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            type=str,
            default=None,
            help="Override WEBHOOK_BASE_URL (e.g. https://api.weel.uz)",
        )

    def handle(self, *args, **options):
        base_url = options["base_url"] or getattr(settings, "WEBHOOK_BASE_URL", "")
        if not base_url:
            self.stderr.write(self.style.ERROR(
                "WEBHOOK_BASE_URL is not set. Pass --base-url or set the env variable."
            ))
            return

        if not getattr(settings, "BOT_TOKEN", None):
            self.stderr.write(
                self.style.ERROR("TELEGRAM_BOT_TOKEN_APP is not configured.")
            )
            return

        self.stdout.write(f"Setting webhook with base URL: {base_url}")
        asyncio.run(set_webhook(base_url))
        self.stdout.write(self.style.SUCCESS("Webhook registered successfully."))
