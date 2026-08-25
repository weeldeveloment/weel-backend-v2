"""Import the Hotelios catalogue on demand.

    python manage.py sync_hotelios_inventory                 # everything
    python manage.py sync_hotelios_inventory --scope hotels  # one phase
    python manage.py sync_hotelios_inventory --scope services --hotel-ids 4 130
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.hotels import sync
from apps.hotels.client import HoteliosError, get_client

SCOPES = ("all", "references", "geography", "hotels", "room_types", "services")


class Command(BaseCommand):
    help = "Sync the Hotelios static inventory into the local tables"

    def add_arguments(self, parser):
        parser.add_argument(
            "--scope",
            default="all",
            choices=SCOPES,
            help="Which phase to run (default: all).",
        )
        parser.add_argument(
            "--hotel-ids",
            nargs="*",
            type=int,
            help="Restrict the 'services' scope to these hotels.",
        )
        parser.add_argument(
            "--keep-missing",
            action="store_true",
            help="Do not retire hotels the catalogue no longer lists.",
        )

    def handle(self, *args, **options):
        scope = options["scope"]
        client = get_client()

        try:
            if scope == "all":
                results = sync.sync_all(client)
            elif scope == "references":
                results = [sync.sync_references(client)]
            elif scope == "geography":
                results = [sync.sync_geography(client)]
            elif scope == "hotels":
                results = [
                    sync.sync_hotels(client, deactivate_missing=not options["keep_missing"])
                ]
            elif scope == "room_types":
                results = [sync.sync_room_types(client)]
            else:
                results = [
                    sync.sync_hotel_services(client, hotel_ids=options.get("hotel_ids"))
                ]
        except HoteliosError as exc:
            raise CommandError(f"Hotelios sync failed: {exc}") from exc

        for result in results:
            self.stdout.write(
                f"  {result['scope']}: {result['records']} records (run #{result['run_id']})"
            )
        self.stdout.write(self.style.SUCCESS("Hotelios inventory sync complete."))
