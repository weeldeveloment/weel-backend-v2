from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.bookingcom.service import sync_all_enabled_reservations


class Command(BaseCommand):
    help = "Synchronize Booking.com reservations for all enabled PMS properties."

    def handle(self, *args, **options):
        results = sync_all_enabled_reservations()
        self.stdout.write(self.style.SUCCESS(f"Synchronized {len(results)} Booking.com property sync(s)."))
