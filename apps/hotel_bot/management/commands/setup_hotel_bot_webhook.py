import asyncio

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Register the hotel bot webhook with Telegram"

    def add_arguments(self, parser):
        parser.add_argument("base_url", type=str, help="Base URL, e.g. https://api.weel.uz")

    def handle(self, *args, **options):
        from apps.hotel_bot.setup import set_webhook
        base_url = options["base_url"]
        asyncio.run(set_webhook(base_url))
        self.stdout.write(self.style.SUCCESS(f"Hotel bot webhook registered for {base_url}"))
