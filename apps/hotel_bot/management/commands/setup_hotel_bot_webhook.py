import asyncio
import sys

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register the hotel bot webhook with Telegram"

    def add_arguments(self, parser):
        parser.add_argument("base_url", type=str, help="Base URL, e.g. https://api.weel.uz")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with non-zero status code if webhook setup fails.",
        )

    def handle(self, *args, **options):
        from apps.hotel_bot.setup import set_webhook
        base_url = options["base_url"]
        try:
            asyncio.run(set_webhook(base_url))
        except Exception as exc:
            msg = str(exc)
            token = getattr(settings, "HOTEL_BOT_TOKEN", "") or ""
            if token:
                msg = msg.replace(token, "<REDACTED>")
            self.stdout.write(self.style.WARNING(
                f"Hotel bot webhook setup failed: {msg}. Continuing startup."
            ))
            if options.get("strict"):
                sys.exit(1)
            return
        self.stdout.write(self.style.SUCCESS(f"Hotel bot webhook registered for {base_url}"))
