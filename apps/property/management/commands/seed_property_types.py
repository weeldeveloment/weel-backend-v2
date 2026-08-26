"""
PropertyType va PropertyService larni default qiymatlar bilan yaratadi.
Ishlatish: python manage.py seed_property_types
"""
from __future__ import annotations

from uuid import uuid4

from django.core.management.base import BaseCommand
from django.utils import timezone

from shared.raw.db import execute, fetch_one, table_exists


DEFAULT_ICON_PATH = "property/icons/default.svg"

PROPERTY_TYPES = [
    {"title_en": "Apartment", "title_ru": "Квартира", "title_uz": "Kvartira"},
    {"title_en": "Cottages", "title_ru": "Дача", "title_uz": "Dacha"},

]

SERVICES_BY_TYPE = {
    "Apartment": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Parking", "title_ru": "Парковка", "title_uz": "Parkovka"},
    ],
    "Cottages": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Barbecue", "title_ru": "Мангал", "title_uz": "Barbekyu"},
    ],
    "House": [
        {"title_en": "Wi-Fi", "title_ru": "Wi-Fi", "title_uz": "Wi-Fi"},
        {"title_en": "Parking", "title_ru": "Парковка", "title_uz": "Parkovka"},
    ],
}


class Command(BaseCommand):
    help = "PropertyType va PropertyService larni default qiymatlar bilan yaratadi."

    def handle(self, *args, **options):
        property_type_table = "property_propertytype"
        property_service_table = "property_propertyservice"

        if not table_exists(property_type_table) or not table_exists(property_service_table):
            self.stdout.write(
                self.style.WARNING(
                    "Legacy property type/service tables are not available in current schema."
                )
            )
            return

        created_count = 0
        service_count = 0
        now = timezone.now()

        for item in PROPERTY_TYPES:
            type_row = fetch_one(
                f"""
                SELECT id, guid
                FROM public.{property_type_table}
                WHERE LOWER(title_en) = LOWER(%s)
                LIMIT 1
                """,
                [item["title_en"]],
            )
            if type_row is None:
                type_row = fetch_one(
                    f"""
                    INSERT INTO public.{property_type_table} (
                        guid,
                        created_at,
                        updated_at,
                        title_en,
                        title_ru,
                        title_uz,
                        icon
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, guid
                    """,
                    [
                        uuid4(),
                        now,
                        now,
                        item["title_en"],
                        item["title_ru"],
                        item["title_uz"],
                        DEFAULT_ICON_PATH,
                    ],
                )
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"PropertyType yaratildi: {item['title_en']} (guid={type_row['guid']})"
                    )
                )
            else:
                self.stdout.write(f"PropertyType mavjud: {item['title_en']} (guid={type_row['guid']})")

            for service in SERVICES_BY_TYPE.get(item["title_en"], []):
                exists = fetch_one(
                    f"""
                    SELECT id
                    FROM public.{property_service_table}
                    WHERE property_type_id = %s
                      AND LOWER(title_en) = LOWER(%s)
                    LIMIT 1
                    """,
                    [type_row["id"], service["title_en"]],
                )
                if exists:
                    continue
                execute(
                    f"""
                    INSERT INTO public.{property_service_table} (
                        guid,
                        created_at,
                        updated_at,
                        title_en,
                        title_ru,
                        title_uz,
                        property_type_id,
                        icon
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        uuid4(),
                        now,
                        now,
                        service["title_en"],
                        service["title_ru"],
                        service["title_uz"],
                        type_row["id"],
                        DEFAULT_ICON_PATH,
                    ],
                )
                service_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Tugadi: {created_count} ta PropertyType, {service_count} ta PropertyService yangi yaratildi."
            )
        )
