from django.core.management.base import BaseCommand
from django.db import transaction

from property.models import Property
from property.pricing import upsert_uniform_monthly_prices


class Command(BaseCommand):
    help = (
        "Backfill missing PropertyPrice rows for properties that still have legacy Property.price value."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear-legacy-price",
            action="store_true",
            help="Set Property.price=NULL after successful backfill.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        clear_legacy_price = options["clear_legacy_price"]
        queryset = (
            Property.objects.filter(price__isnull=False)
            .exclude(property_price__isnull=False)
            .distinct()
            .order_by("id")
        )

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No properties need backfill."))
            return

        updated = 0
        for prop in queryset.iterator():
            upsert_uniform_monthly_prices(prop, prop.price)
            if clear_legacy_price:
                prop.price = None
                prop.save(update_fields=["price"])
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill completed for {updated}/{total} properties."
            )
        )
