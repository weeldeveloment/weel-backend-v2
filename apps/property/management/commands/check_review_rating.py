"""
Review va reytingni tekshirish: 4+ balli review qaysi propertyda va u API roʻyxatida chiqadimi?
Ishlatish: python manage.py check_review_rating
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from shared.raw.db import fetch_all, table_exists


class Command(BaseCommand):
    help = "4+ balli review qaysi propertyda ekanligini va u tekshirilgan roʻyxatda bor-yoʻqligini koʻrsatadi."

    def handle(self, *args, **options):
        if not (table_exists("review") and table_exists("apartment") and table_exists("cottage")):
            self.stdout.write(
                self.style.WARNING(
                    "Required normalized tables (review/apartment/cottage) are not available."
                )
            )
            return

        reviews = fetch_all(
            """
            SELECT
                r.rating,
                r.comment,
                r.is_hidden,
                COALESCE(a.guid, c.guid) AS property_guid,
                COALESCE(a.title, c.title) AS property_title,
                COALESCE(a.is_verified, c.is_verified, FALSE) AS is_verified,
                COALESCE(a.is_archived, c.is_archived, FALSE) AS is_archived
            FROM public.review r
            LEFT JOIN public.apartment a ON a.id = r.apartment_id
            LEFT JOIN public.cottage c ON c.id = r.cottage_id
            WHERE r.rating >= 4
              AND r.rating IS NOT NULL
              AND COALESCE(r.is_hidden, FALSE) = FALSE
            ORDER BY r.rating DESC, r.created_at DESC
            """
        )

        if not reviews:
            self.stdout.write(
                self.style.WARNING(
                    "4+ balli va yashirin boʻlmagan review topilmadi."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"4+ balli (yashirin emas) reviewlar: {len(reviews)} ta\n")
        )
        for row in reviews:
            in_list = bool(row.get("is_verified")) and not bool(row.get("is_archived"))
            status = "✓ API roʻyxatida chiqadi" if in_list else "✗ API roʻyxatida YOʻQ (tekshirilmagan yoki arxiv)"
            self.stdout.write(
                f"  Property: {row.get('property_title')} (guid={row.get('property_guid')})\n"
                f"    Reyting: {row.get('rating')}, Hide: {row.get('is_hidden')}\n"
                f"    {status}\n"
            )
