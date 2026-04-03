"""
Bir martalik: mavjud jadvallardan norm_* ga to'ldirish.

    python manage.py sync_norm_from_django
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from shared.raw.db import fetch_all, table_exists

from norm_store.sync import (
    ensure_norm_customer,
    ensure_norm_partner,
    norm_enabled,
    sync_booking_to_norm,
    sync_property_to_norm,
)


class Command(BaseCommand):
    help = "Backfill norm_* tables from current datastore rows."

    def handle(self, *args, **options):
        if not norm_enabled():
            self.stderr.write("Set USE_NORM_DATASTORE=1 in environment first.")
            return

        if not table_exists("users"):
            self.stderr.write("users table is not available.")
            return

        client_rows = fetch_all(
            """
            SELECT id, guid, first_name, last_name, phone_number, is_active
            FROM public.users
            WHERE role = 'client'
            ORDER BY id
            """
        )
        synced_customers = 0
        for row in client_rows:
            if ensure_norm_customer(row):
                synced_customers += 1
        self.stdout.write(f"norm_customers synced: {synced_customers}")

        partner_rows = fetch_all(
            """
            SELECT
                id,
                guid,
                first_name,
                last_name,
                username,
                phone_number,
                email,
                is_active,
                is_verified,
                verified_by_user_id AS verified_by_id,
                verified_at
            FROM public.users
            WHERE role = 'partner'
            ORDER BY id
            """
        )
        partner_by_id = {int(row["id"]): row for row in partner_rows}
        synced_partners = 0
        for row in partner_rows:
            if ensure_norm_partner(row):
                synced_partners += 1
        self.stdout.write(f"norm_partners synced: {synced_partners}")

        property_rows = []
        if table_exists("property_map") and table_exists("apartment"):
            property_rows.extend(
                fetch_all(
                    """
                    SELECT
                        pm.legacy_property_id AS id,
                        a.guid,
                        a.title,
                        a.currency,
                        a.verification_status,
                        a.is_verified,
                        a.is_archived,
                        a.region_id,
                        a.district_id,
                        a.city,
                        a.country,
                        a.latitude,
                        a.longitude,
                        a.partner_user_id AS partner_id
                    FROM public.property_map pm
                    JOIN public.apartment a
                      ON pm.target_table = 'apartment'
                     AND pm.target_id = a.id
                    ORDER BY pm.legacy_property_id
                    """
                )
            )
        if table_exists("property_map") and table_exists("cottage"):
            property_rows.extend(
                fetch_all(
                    """
                    SELECT
                        pm.legacy_property_id AS id,
                        c.guid,
                        c.title,
                        c.currency,
                        c.verification_status,
                        c.is_verified,
                        c.is_archived,
                        c.region_id,
                        c.district_id,
                        c.city,
                        c.country,
                        c.latitude,
                        c.longitude,
                        c.partner_user_id AS partner_id
                    FROM public.property_map pm
                    JOIN public.cottage c
                      ON pm.target_table = 'cottage'
                     AND pm.target_id = c.id
                    ORDER BY pm.legacy_property_id
                    """
                )
            )
        synced_properties = 0
        for row in property_rows:
            partner_id = row.get("partner_id")
            if partner_id is not None:
                row["partner"] = partner_by_id.get(int(partner_id))
            sync_property_to_norm(row)
            synced_properties += 1
        self.stdout.write(f"norm_properties synced: {synced_properties}")

        booking_rows = []
        if table_exists("booking"):
            booking_rows = fetch_all(
                """
                SELECT
                    id,
                    guid,
                    booking_number,
                    check_in,
                    check_out,
                    adults,
                    children,
                    babies,
                    status,
                    cancellation_reason,
                    confirmed_at,
                    cancelled_at,
                    completed_at,
                    reminder_sent,
                    payment_reminder_stage,
                    client_user_id,
                    property_apartment_id,
                    property_cottage_id
                FROM public.booking
                ORDER BY id
                """
            )
        synced_bookings = 0
        for row in booking_rows:
            sync_booking_to_norm(row, old_status=None)
            synced_bookings += 1
        self.stdout.write(f"norm_bookings synced: {synced_bookings}")

        self.stdout.write(self.style.SUCCESS("Done."))
