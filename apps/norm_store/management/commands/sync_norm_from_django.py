"""
Bir martalik: mavjud Django jadvallaridan norm_* ga to'ldirish.
.env da USE_NORM_DATASTORE=1 bo'lishi kerak.

    python manage.py sync_norm_from_django
"""
from django.core.management.base import BaseCommand

from norm_store.sync import (
    ensure_norm_customer,
    ensure_norm_partner,
    norm_enabled,
    sync_booking_to_norm,
    sync_property_to_norm,
)


class Command(BaseCommand):
    help = "Backfill norm_* from users_client, property_property, booking_booking, ..."

    def handle(self, *args, **options):
        if not norm_enabled():
            self.stderr.write("Set USE_NORM_DATASTORE=1 in environment first.")
            return

        from users.models.clients import Client
        from users.models.partners import Partner
        from property.models import Property
        from booking.models import Booking

        n = 0
        for c in Client.objects.iterator():
            if ensure_norm_customer(c):
                n += 1
        self.stdout.write(f"norm_customers synced: {n}")

        n = 0
        for p in Partner.objects.iterator():
            if ensure_norm_partner(p):
                n += 1
        self.stdout.write(f"norm_partners synced: {n}")

        n = 0
        for prop in Property.objects.prefetch_related("property_price").iterator(chunk_size=50):
            sync_property_to_norm(prop)
            n += 1
        self.stdout.write(f"norm_properties synced: {n}")

        n = 0
        for b in Booking.objects.select_related("client", "property").iterator():
            sync_booking_to_norm(b, old_status=None)
            n += 1
        self.stdout.write(f"norm_bookings synced: {n}")

        self.stdout.write(self.style.SUCCESS("Done."))
