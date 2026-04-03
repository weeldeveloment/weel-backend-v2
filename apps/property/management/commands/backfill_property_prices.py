from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from shared.date import month_end, month_start
from shared.raw.db import execute, fetch_all, fetch_one, table_exists


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

    @staticmethod
    def _resolve_table(*candidates: str) -> str | None:
        for name in candidates:
            if table_exists(name):
                return name
        return None

    @staticmethod
    def _month_ranges(reference_date: date) -> list[tuple[date, date]]:
        this_month = month_start(reference_date)
        next_month = month_start(reference_date.replace(day=1) + timedelta(days=32))
        return [
            (this_month, month_end(this_month)),
            (next_month, month_end(next_month)),
        ]

    @staticmethod
    def _upsert_month_price(
        *,
        price_table: str,
        property_id: int,
        month_from: date,
        month_to: date,
        base_price: Decimal,
        now,
    ) -> None:
        existing = fetch_one(
            f"""
            SELECT id
            FROM public.{price_table}
            WHERE property_id = %s
              AND month_from = %s
            LIMIT 1
            """,
            [property_id, month_from],
        )
        if existing:
            execute(
                f"""
                UPDATE public.{price_table}
                SET month_to = %s,
                    price_per_person = %s,
                    price_on_working_days = %s,
                    price_on_weekends = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                [month_to, Decimal("0"), base_price, base_price, now, existing["id"]],
            )
            return

        execute(
            f"""
            INSERT INTO public.{price_table} (
                guid,
                created_at,
                updated_at,
                property_id,
                month_from,
                month_to,
                price_per_person,
                price_on_working_days,
                price_on_weekends
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                uuid4(),
                now,
                now,
                property_id,
                month_from,
                month_to,
                Decimal("0"),
                base_price,
                base_price,
            ],
        )

    @transaction.atomic
    def handle(self, *args, **options):
        clear_legacy_price = options["clear_legacy_price"]
        property_table = self._resolve_table("property_property")
        price_table = self._resolve_table("property_propertyprice")

        if not property_table or not price_table:
            self.stdout.write(
                self.style.WARNING(
                    "Legacy property tables are not available in current schema. Nothing to backfill."
                )
            )
            return

        rows = fetch_all(
            f"""
            SELECT p.id, p.price
            FROM public.{property_table} p
            WHERE p.price IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.{price_table} pp
                  WHERE pp.property_id = p.id
              )
            ORDER BY p.id
            """
        )

        total = len(rows)
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No properties need backfill."))
            return

        updated = 0
        now = timezone.now()
        month_ranges = self._month_ranges(timezone.localdate())
        for row in rows:
            property_id = int(row["id"])
            base_price = Decimal(str(row["price"]))
            for month_from, month_to in month_ranges:
                self._upsert_month_price(
                    price_table=price_table,
                    property_id=property_id,
                    month_from=month_from,
                    month_to=month_to,
                    base_price=base_price,
                    now=now,
                )
            if clear_legacy_price:
                execute(
                    f"""
                    UPDATE public.{property_table}
                    SET price = NULL,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    [now, property_id],
                )
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill completed for {updated}/{total} properties."
            )
        )
