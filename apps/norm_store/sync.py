"""
Write-through sync from Django/normalized rows to norm_* tables when USE_NORM_DATASTORE is on.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists

logger = logging.getLogger(__name__)


def norm_enabled() -> bool:
    return bool(getattr(settings, "USE_NORM_DATASTORE", False))


def _get(instance, key: str, default=None):
    if instance is None:
        return default
    if isinstance(instance, dict):
        return instance.get(key, default)
    return getattr(instance, key, default)


def _upsert_with_legacy_id(
    *,
    table: str,
    legacy_field: str,
    legacy_value,
    data: dict,
) -> dict | None:
    if legacy_value is None or not table_exists(table):
        return None

    now = timezone.now()
    existing = fetch_one(
        f"""
        SELECT id
        FROM public.{table}
        WHERE {legacy_field} = %s
        LIMIT 1
        """,
        [legacy_value],
    )
    if existing:
        assignments = ", ".join(f"{key} = %s" for key in data.keys())
        execute(
            f"""
            UPDATE public.{table}
            SET {assignments},
                updated_at = %s
            WHERE id = %s
            """,
            [*data.values(), now, existing["id"]],
        )
        return {"id": int(existing["id"])}

    columns = ["guid", "created_at", "updated_at", legacy_field, *data.keys()]
    placeholders = ", ".join(["%s"] * len(columns))
    inserted = fetch_one(
        f"""
        INSERT INTO public.{table} ({", ".join(columns)})
        VALUES ({placeholders})
        RETURNING id
        """,
        [uuid4(), now, now, legacy_value, *data.values()],
    )
    if not inserted:
        return None
    return {"id": int(inserted["id"])}


def _resolve_legacy_property_id_from_booking(booking) -> int | None:
    direct = _get(booking, "property_id")
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            return None

    if table_exists("property_map"):
        apartment_id = _get(booking, "property_apartment_id")
        if apartment_id is not None:
            row = fetch_one(
                """
                SELECT legacy_property_id
                FROM public.property_map
                WHERE target_table = 'apartment'
                  AND target_id = %s
                LIMIT 1
                """,
                [apartment_id],
            )
            if row and row.get("legacy_property_id") is not None:
                return int(row["legacy_property_id"])

        cottage_id = _get(booking, "property_cottage_id")
        if cottage_id is not None:
            row = fetch_one(
                """
                SELECT legacy_property_id
                FROM public.property_map
                WHERE target_table = 'cottage'
                  AND target_id = %s
                LIMIT 1
                """,
                [cottage_id],
            )
            if row and row.get("legacy_property_id") is not None:
                return int(row["legacy_property_id"])
    return None


def ensure_norm_customer(client):
    if not norm_enabled():
        return None

    client_id = _get(client, "id")
    if client_id is None:
        return None

    try:
        return _upsert_with_legacy_id(
            table="norm_customers",
            legacy_field="legacy_client_id",
            legacy_value=int(client_id),
            data={
                "legacy_client_guid": _get(client, "guid"),
                "first_name": _get(client, "first_name", "") or "",
                "last_name": _get(client, "last_name", "") or "",
                "phone_number": _get(client, "phone_number", "") or "",
                "is_active": bool(_get(client, "is_active", True)),
            },
        )
    except DatabaseError as e:
        logger.exception("norm_store.ensure_norm_customer: %s", e)
        return None


def ensure_norm_partner(partner):
    if not norm_enabled():
        return None

    partner_id = _get(partner, "id")
    if partner_id is None:
        return None

    try:
        return _upsert_with_legacy_id(
            table="norm_partners",
            legacy_field="legacy_partner_id",
            legacy_value=int(partner_id),
            data={
                "legacy_partner_guid": _get(partner, "guid"),
                "first_name": _get(partner, "first_name", "") or "",
                "last_name": _get(partner, "last_name", "") or "",
                "username": _get(partner, "username", "") or "",
                "phone_number": _get(partner, "phone_number", "") or "",
                "email": _get(partner, "email", "") or "",
                "is_active": bool(_get(partner, "is_active", True)),
                "is_verified": _get(partner, "is_verified"),
                "verified_by_admin_id": _get(partner, "verified_by_id"),
                "verified_at": _get(partner, "verified_at"),
            },
        )
    except DatabaseError as e:
        logger.exception("norm_store.ensure_norm_partner: %s", e)
        return None


def sync_property_to_norm(prop) -> None:
    if not norm_enabled():
        return

    legacy_property_id = _get(prop, "id")
    if legacy_property_id is None:
        return

    try:
        partner = _get(prop, "partner")
        npartner = ensure_norm_partner(partner)

        location = _get(prop, "property_location")
        data = {
            "legacy_property_guid": _get(prop, "guid"),
            "title": (_get(prop, "title", "") or "")[:75],
            "currency": str(_get(prop, "currency", "USD") or "USD")[:3],
            "verification_status": str(_get(prop, "verification_status", "") or "")[:10],
            "is_verified": bool(_get(prop, "is_verified", False)),
            "is_archived": bool(_get(prop, "is_archived", False)),
            "region_id": _get(prop, "region_id"),
            "district_id": _get(prop, "district_id"),
            "city": (_get(location, "city", _get(prop, "city", "")) or "")[:100],
            "country": (_get(location, "country", _get(prop, "country", "")) or "")[:100],
            "latitude": _get(location, "latitude", _get(prop, "latitude")),
            "longitude": _get(location, "longitude", _get(prop, "longitude")),
            "partner_id": npartner["id"] if npartner else None,
        }
        norm_property = _upsert_with_legacy_id(
            table="norm_properties",
            legacy_field="legacy_property_id",
            legacy_value=int(legacy_property_id),
            data=data,
        )
        if not norm_property:
            return

        if table_exists("property_propertyprice") and table_exists("norm_property_prices"):
            price_rows = fetch_all(
                """
                SELECT
                    id,
                    month_from,
                    month_to,
                    price_per_person,
                    price_on_working_days,
                    price_on_weekends
                FROM public.property_propertyprice
                WHERE property_id = %s
                """,
                [legacy_property_id],
            )
            for price_row in price_rows:
                _upsert_with_legacy_id(
                    table="norm_property_prices",
                    legacy_field="legacy_property_price_id",
                    legacy_value=int(price_row["id"]),
                    data={
                        "property_id": norm_property["id"],
                        "month_from": price_row["month_from"],
                        "month_to": price_row["month_to"],
                        "price_per_person": price_row["price_per_person"],
                        "price_on_working_days": price_row["price_on_working_days"],
                        "price_on_weekends": price_row["price_on_weekends"],
                    },
                )
    except DatabaseError as e:
        logger.exception("norm_store.sync_property_to_norm: %s", e)


def sync_booking_to_norm(booking, old_status: str | None) -> None:
    if not norm_enabled():
        return

    legacy_booking_id = _get(booking, "id")
    legacy_booking_guid = _get(booking, "guid")
    if legacy_booking_guid is None:
        return

    try:
        customer = _get(booking, "client")
        if customer is None and _get(booking, "client_user_id") is not None:
            customer = fetch_one(
                """
                SELECT id, guid, first_name, last_name, phone_number, is_active
                FROM public.users
                WHERE id = %s
                LIMIT 1
                """,
                [_get(booking, "client_user_id")],
            )
        norm_customer = ensure_norm_customer(customer)
        if not norm_customer:
            return

        legacy_property_id = _resolve_legacy_property_id_from_booking(booking)
        if legacy_property_id is None:
            return
        norm_property = fetch_one(
            """
            SELECT id
            FROM public.norm_properties
            WHERE legacy_property_id = %s
            LIMIT 1
            """,
            [legacy_property_id],
        )
        if not norm_property:
            prop = _get(booking, "property")
            if prop is not None:
                sync_property_to_norm(prop)
                norm_property = fetch_one(
                    """
                    SELECT id
                    FROM public.norm_properties
                    WHERE legacy_property_id = %s
                    LIMIT 1
                    """,
                    [legacy_property_id],
                )
        if not norm_property:
            return

        _upsert_with_legacy_id(
            table="norm_bookings",
            legacy_field="legacy_booking_guid",
            legacy_value=legacy_booking_guid,
            data={
                "legacy_booking_id": legacy_booking_id,
                "booking_number": _get(booking, "booking_number"),
                "check_in": _get(booking, "check_in"),
                "check_out": _get(booking, "check_out"),
                "adults": _get(booking, "adults"),
                "children": _get(booking, "children"),
                "babies": _get(booking, "babies"),
                "current_status": _get(booking, "status"),
                "cancellation_reason": (_get(booking, "cancellation_reason", "") or "")[:100],
                "confirmed_at": _get(booking, "confirmed_at"),
                "cancelled_at": _get(booking, "cancelled_at"),
                "completed_at": _get(booking, "completed_at"),
                "reminder_sent": bool(_get(booking, "reminder_sent", False)),
                "payment_reminder_stage": (_get(booking, "payment_reminder_stage", "") or "")[:10],
                "customer_id": norm_customer["id"],
                "property_id": int(norm_property["id"]),
            },
        )

        current_status = _get(booking, "status")
        if old_status is not None and current_status != old_status and table_exists("norm_booking_status_history"):
            norm_booking = fetch_one(
                """
                SELECT id
                FROM public.norm_bookings
                WHERE legacy_booking_guid = %s
                LIMIT 1
                """,
                [legacy_booking_guid],
            )
            if norm_booking:
                now = timezone.now()
                execute(
                    """
                    INSERT INTO public.norm_booking_status_history (
                        guid,
                        created_at,
                        updated_at,
                        from_status,
                        to_status,
                        reason,
                        source,
                        changed_at,
                        booking_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        uuid4(),
                        now,
                        now,
                        old_status or "",
                        current_status or "",
                        (_get(booking, "cancellation_reason", "") or "")[:100],
                        "api",
                        now,
                        int(norm_booking["id"]),
                    ],
                )
    except DatabaseError as e:
        logger.exception("norm_store.sync_booking_to_norm: %s", e)


def sync_plum_to_norm(tx) -> None:
    if not norm_enabled():
        return

    legacy_tx_id = _get(tx, "id")
    if legacy_tx_id is None:
        return

    try:
        _upsert_with_legacy_id(
            table="norm_payment_transactions",
            legacy_field="legacy_plum_transaction_id",
            legacy_value=int(legacy_tx_id),
            data={
                "legacy_plum_transaction_guid": _get(tx, "guid"),
                "provider_transaction_id": _get(tx, "transaction_id"),
                "provider_hold_id": _get(tx, "hold_id"),
                "amount": _get(tx, "amount"),
                "type": _get(tx, "type"),
                "status": _get(tx, "status"),
                "card_id": _get(tx, "card_id") or "",
                "extra_id": _get(tx, "extra_id") or "",
            },
        )
    except DatabaseError as e:
        logger.exception("norm_store.sync_plum_to_norm: %s", e)
