"""Persistence for the Hotelios inventory and for bookings made through it.

The inventory half is a mirror: Hotelios owns the catalogue, and these
functions write what a sync pass read. The booking half is a local record of
orders whose authoritative state is `booking/read`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.hotels.raw.tables import (
    HOTELIOS_BOOKING_EVENT_TABLE,
    HOTELIOS_BOOKING_ROOM_TABLE,
    HOTELIOS_BOOKING_TABLE,
    HOTELIOS_CITY_TABLE,
    HOTELIOS_CURRENCY_TABLE,
    HOTELIOS_HOTEL_TABLE,
    HOTELIOS_REGION_TABLE,
    HOTELIOS_ROOM_TYPE_TABLE,
    HOTELIOS_STAR_TABLE,
    HOTELIOS_SYNC_RUN_TABLE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def names_to_map(names: Any) -> dict[str, str]:
    """`[{"locale": "uz", "value": "..."}]` → `{"uz": "..."}`.

    Hotelios sends the localised strings as a list so the set of locales can
    grow. We store a map because every read is "give me this name in the user's
    language", and a map answers that without scanning.
    """
    if isinstance(names, dict):
        return {k: v for k, v in names.items() if isinstance(v, str)}
    result: dict[str, str] = {}
    for entry in names or []:
        if not isinstance(entry, dict):
            continue
        locale, value = entry.get("locale"), entry.get("value")
        if locale and value is not None:
            result[str(locale)] = value
    return result


def pick_name(names: Any, language: str | None = None) -> str | None:
    """The best available string from a names map for the requested language."""
    mapping = names_to_map(names)
    if not mapping:
        return None
    order = [language] if language else []
    order += [locale for locale in ("en", "ru", "uz") if locale not in order]
    for locale in order:
        if locale and mapping.get(locale):
            return mapping[locale]
    return next(iter(mapping.values()), None)


def _parse_provider_datetime(value: Any) -> datetime | None:
    """Hotelios timestamps: `2025/11/25 14:00`, `2025/10/01 10:01`, ISO-8601."""
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else value.replace(tzinfo=dt_timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("hotels: could not parse datetime %r", value)
        return None
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=dt_timezone.utc)


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        logger.warning("hotels: could not parse amount %r", value)
        return None


# ---------------------------------------------------------------------------
# Reference lists
# ---------------------------------------------------------------------------

def upsert_reference_rows(
    table: str,
    rows: Iterable[dict[str, Any]],
    *,
    with_filter_flag: bool = False,
) -> int:
    """Write one of the flat id/names reference lists."""
    count = 0
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            continue
        names = _to_json(names_to_map(row.get("names")))
        if with_filter_flag:
            execute(
                f"""
                INSERT INTO {table} (id, names, filter_flag, synced_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    names = EXCLUDED.names,
                    filter_flag = EXCLUDED.filter_flag,
                    synced_at = NOW()
                """,
                [row_id, names, bool(row.get("filter_flag"))],
            )
        else:
            execute(
                f"""
                INSERT INTO {table} (id, names, synced_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    names = EXCLUDED.names,
                    synced_at = NOW()
                """,
                [row_id, names],
            )
        count += 1
    return count


def upsert_stars(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if row.get("id") is None:
            continue
        execute(
            f"""
            INSERT INTO {HOTELIOS_STAR_TABLE} (id, name, synced_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, synced_at = NOW()
            """,
            [row["id"], str(row.get("name") or row["id"])],
        )
        count += 1
    return count


def upsert_currencies(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        execute(
            f"""
            INSERT INTO {HOTELIOS_CURRENCY_TABLE} (code, name, synced_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, synced_at = NOW()
            """,
            [code, row.get("name") or code],
        )
        count += 1
    return count



def upsert_regions(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if row.get("id") is None:
            continue
        names = names_to_map(row.get("names"))
        execute(
            f"""
            INSERT INTO {HOTELIOS_REGION_TABLE} (id, country_id, names, name_en, synced_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                country_id = EXCLUDED.country_id,
                names = EXCLUDED.names,
                name_en = EXCLUDED.name_en,
                synced_at = NOW()
            """,
            [row["id"], row.get("country_id"), _to_json(names), pick_name(names, "en")],
        )
        count += 1
    return count


def upsert_cities(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        if row.get("id") is None:
            continue
        names = names_to_map(row.get("names"))
        execute(
            f"""
            INSERT INTO {HOTELIOS_CITY_TABLE} (id, region_id, names, name_en, synced_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                region_id = EXCLUDED.region_id,
                names = EXCLUDED.names,
                name_en = EXCLUDED.name_en,
                synced_at = NOW()
            """,
            [row["id"], row.get("region_id"), _to_json(names), pick_name(names, "en")],
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Hotels and room types
# ---------------------------------------------------------------------------

def upsert_hotel(payload: dict[str, Any]) -> None:
    names = names_to_map(payload.get("names"))
    execute(
        f"""
        INSERT INTO {HOTELIOS_HOTEL_TABLE} (
            id, hotel_type_id, city_id, star_id, currency,
            latitude, longitude, postal_code,
            names, name_en, address, description,
            check_in, check_out, guest_age_rules,
            facilities, photos, nearby_places,
            provider_updated_at, raw, synced_at, is_active
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, NOW(), TRUE
        )
        ON CONFLICT (id) DO UPDATE SET
            hotel_type_id = EXCLUDED.hotel_type_id,
            city_id = EXCLUDED.city_id,
            star_id = EXCLUDED.star_id,
            currency = EXCLUDED.currency,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            postal_code = EXCLUDED.postal_code,
            names = EXCLUDED.names,
            name_en = EXCLUDED.name_en,
            address = EXCLUDED.address,
            description = EXCLUDED.description,
            check_in = EXCLUDED.check_in,
            check_out = EXCLUDED.check_out,
            guest_age_rules = EXCLUDED.guest_age_rules,
            facilities = EXCLUDED.facilities,
            photos = EXCLUDED.photos,
            nearby_places = EXCLUDED.nearby_places,
            provider_updated_at = EXCLUDED.provider_updated_at,
            raw = EXCLUDED.raw,
            synced_at = NOW(),
            is_active = TRUE
        """,
        [
            payload.get("id"),
            payload.get("hotel_type_id"),
            payload.get("city_id"),
            payload.get("star_id"),
            (payload.get("currency") or "").lower() or None,
            payload.get("latitude"),
            payload.get("longitude"),
            payload.get("postal_code"),
            _to_json(names),
            pick_name(names, "en"),
            _to_json(names_to_map(payload.get("address"))),
            _to_json(names_to_map(payload.get("description"))),
            _to_json(payload.get("check_in") or []),
            _to_json(payload.get("check_out") or []),
            _to_json(payload.get("guest_age_rules") or []),
            _to_json(payload.get("hotel_facilities") or []),
            _to_json(payload.get("hotel_photos") or []),
            _to_json(payload.get("hotel_nearby_places") or []),
            _parse_provider_datetime(payload.get("updated_on")),
            _to_json(payload),
        ],
    )


def set_hotel_services_in_room(*, hotel_id: int, services: list[dict[str, Any]]) -> None:
    execute(
        f"""
        UPDATE {HOTELIOS_HOTEL_TABLE}
        SET services_in_room = %s, synced_at = NOW()
        WHERE id = %s
        """,
        [_to_json(services), hotel_id],
    )


def upsert_room_type(payload: dict[str, Any]) -> None:
    names = names_to_map(payload.get("names"))
    execute(
        f"""
        INSERT INTO {HOTELIOS_ROOM_TYPE_TABLE} (
            hotel_id, room_type_id, holding_capacity, bed_type, extra_bed, area,
            names, name_en, description, photos, equipments, raw, synced_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, NOW()
        )
        ON CONFLICT (hotel_id, room_type_id) DO UPDATE SET
            holding_capacity = EXCLUDED.holding_capacity,
            bed_type = EXCLUDED.bed_type,
            extra_bed = EXCLUDED.extra_bed,
            area = EXCLUDED.area,
            names = EXCLUDED.names,
            name_en = EXCLUDED.name_en,
            description = EXCLUDED.description,
            photos = EXCLUDED.photos,
            equipments = EXCLUDED.equipments,
            raw = EXCLUDED.raw,
            synced_at = NOW()
        """,
        [
            payload.get("hotel_id"),
            payload.get("id"),
            payload.get("holding_capacity"),
            payload.get("bed_type"),
            bool(payload.get("extra_bed")),
            payload.get("area"),
            _to_json(names),
            pick_name(names, "en"),
            _to_json(names_to_map(payload.get("descriptions") or payload.get("description"))),
            _to_json(payload.get("room_photos") or []),
            _to_json(payload.get("room_equipments") or []),
            _to_json(payload),
        ],
    )


def deactivate_hotels_missing_since(cutoff: datetime) -> int:
    """Retire hotels a completed full sync did not see.

    Only ever called after a successful pass over the whole catalogue —
    otherwise a sync that died halfway would wipe the inventory.
    """
    return execute(
        f"""
        UPDATE {HOTELIOS_HOTEL_TABLE}
        SET is_active = FALSE
        WHERE is_active AND synced_at < %s
        """,
        [cutoff],
    )


# ---------------------------------------------------------------------------
# Inventory reads
# ---------------------------------------------------------------------------

def fetch_hotel(hotel_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {HOTELIOS_HOTEL_TABLE} WHERE id = %s", [hotel_id]
    )


def fetch_room_types(hotel_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_ROOM_TYPE_TABLE}
        WHERE hotel_id = %s
        ORDER BY room_type_id
        """,
        [hotel_id],
    )



def fetch_hotels(
    *,
    city_id: int | None = None,
    hotel_ids: list[int] | None = None,
    stars: list[int] | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Browse the synced catalogue. Returns the page and the total count."""
    conditions = ["is_active"]
    params: list[Any] = []
    if city_id is not None:
        conditions.append("city_id = %s")
        params.append(city_id)
    if hotel_ids:
        conditions.append("id = __ANY_MARKER__(%s)")
        params.append(hotel_ids)
    if stars:
        conditions.append("star_id = __ANY_MARKER__(%s)")
        params.append(stars)
    if query:
        conditions.append("LOWER(name_en) LIKE %s")
        params.append(f"%{query.lower()}%")

    where = " AND ".join(conditions)
    total_row = fetch_one(
        f"SELECT COUNT(*) AS total FROM {HOTELIOS_HOTEL_TABLE} WHERE {where}", params
    )
    rows = fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_HOTEL_TABLE}
        WHERE {where}
        ORDER BY star_id DESC NULLS LAST, name_en ASC
        LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    )
    return rows, int((total_row or {}).get("total") or 0)


def fetch_cities(*, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """City lookup for the search box. Only cities that actually have hotels.

    A supplier catalogue lists far more cities than it has properties in;
    offering one with nothing bookable in it is a dead end for the user.
    """
    conditions = []
    params: list[Any] = []
    if query:
        conditions.append("LOWER(c.name_en) LIKE %s")
        params.append(f"%{query.lower()}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_all(
        f"""
        SELECT c.*, COUNT(h.id) AS hotel_count
        FROM {HOTELIOS_CITY_TABLE} c
        JOIN {HOTELIOS_HOTEL_TABLE} h ON h.city_id = c.id AND h.is_active
        {where}
        GROUP BY c.id
        ORDER BY COUNT(h.id) DESC, c.name_en ASC
        LIMIT %s
        """,
        [*params, limit],
    )


def fetch_reference(table: str) -> list[dict[str, Any]]:
    return fetch_all(f"SELECT * FROM {table} ORDER BY id")


# ---------------------------------------------------------------------------
# Sync runs
# ---------------------------------------------------------------------------

def start_sync_run(scope: str) -> dict[str, Any]:
    return fetch_one(
        f"""
        INSERT INTO {HOTELIOS_SYNC_RUN_TABLE} (scope, status)
        VALUES (%s, 'running')
        RETURNING *
        """,
        [scope],
    )


def update_sync_run(
    run_id: int,
    *,
    pages_done: int | None = None,
    pages_total: int | None = None,
    records: int | None = None,
) -> None:
    sets, params = [], []
    if pages_done is not None:
        sets.append("pages_done = %s")
        params.append(pages_done)
    if pages_total is not None:
        sets.append("pages_total = %s")
        params.append(pages_total)
    if records is not None:
        sets.append("records = %s")
        params.append(records)
    if not sets:
        return
    execute(
        f"UPDATE {HOTELIOS_SYNC_RUN_TABLE} SET {', '.join(sets)} WHERE id = %s",
        [*params, run_id],
    )


def finish_sync_run(run_id: int, *, status: str, error: str | None = None) -> None:
    execute(
        f"""
        UPDATE {HOTELIOS_SYNC_RUN_TABLE}
        SET status = %s, error = %s, finished_at = NOW()
        WHERE id = %s
        """,
        [status, error, run_id],
    )


def fetch_recent_sync_runs(limit: int = 20) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {HOTELIOS_SYNC_RUN_TABLE} ORDER BY started_at DESC LIMIT %s",
        [limit],
    )


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def create_draft_booking(
    *,
    external_id: str,
    quote_id: str,
    hotel_id: int | None,
    check_in: Any,
    check_out: Any,
    nationality: str | None = None,
    residence: str | None = None,
    is_resident: bool = False,
    comment: str | None = None,
    client_user_id: int | None = None,
    b2b_company_id: int | None = None,
    b2b_user_id: int | None = None,
    b2b_trip_id: int | None = None,
) -> dict[str, Any]:
    """Reserve our own row *before* calling Create.

    `external_id` has to be unique on Hotelios' side and is the only handle we
    have if the Create call times out after they accepted it — so the row that
    owns that id exists first, and the provider id is filled in after.
    """
    return fetch_one(
        f"""
        INSERT INTO {HOTELIOS_BOOKING_TABLE} (
            external_id, quote_id, hotel_id, status,
            check_in, check_out, nationality, residence, is_resident, comment,
            client_user_id, b2b_company_id, b2b_user_id, b2b_trip_id
        ) VALUES (
            %s, %s, %s, 'DRAFT',
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        RETURNING *
        """,
        [
            external_id, quote_id, hotel_id,
            check_in, check_out, nationality, residence, is_resident, comment,
            client_user_id, b2b_company_id, b2b_user_id, b2b_trip_id,
        ],
    )


def attach_provider_booking(
    *,
    booking_id: int,
    provider_booking_id: str,
    price: Any,
    currency: str | None,
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {HOTELIOS_BOOKING_TABLE}
        SET provider_booking_id = %s,
            price = %s,
            currency = COALESCE(%s, currency),
            updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [provider_booking_id, _parse_amount(price), currency, booking_id],
    )


def update_booking_from_provider(
    *,
    booking_id: int,
    payload: dict[str, Any],
    preserve_draft: bool = True,
) -> dict[str, Any] | None:
    """Fold a `booking/read` payload into the local row.

    `DRAFT` is ours, not the provider's. Hotelios reports a created-but-not-yet
    confirmed booking as PENDING, so a plain read would move the row out of
    DRAFT and make the confirm step look as though it had already happened.
    While the booking is still a draft, the provider's status is not applied —
    only the confirm path passes `preserve_draft=False`.
    """
    booking = fetch_one(
        f"""
        UPDATE {HOTELIOS_BOOKING_TABLE}
        SET provider_booking_id = COALESCE(%s, provider_booking_id),
            hotel_id = COALESCE(%s, hotel_id),
            status = CASE
                WHEN %s AND {HOTELIOS_BOOKING_TABLE}.status = 'DRAFT' THEN 'DRAFT'
                ELSE %s
            END,
            check_in = COALESCE(%s, check_in),
            check_out = COALESCE(%s, check_out),
            is_resident = %s,
            price = COALESCE(%s, price),
            currency = COALESCE(%s, currency),
            comment = COALESCE(%s, comment),
            hotel_confirmation_number = %s,
            additional_information = %s,
            provider_created_at = COALESCE(%s, provider_created_at),
            raw = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [
            payload.get("booking_id"),
            payload.get("hotel_id"),
            preserve_draft,
            payload.get("status"),
            _parse_provider_datetime(payload.get("check_in")),
            _parse_provider_datetime(payload.get("check_out")),
            bool(payload.get("is_resident")),
            _parse_amount(payload.get("price")),
            (payload.get("currency") or "").lower() or None,
            payload.get("comment"),
            payload.get("hotel_confirmation_number"),
            _to_json(payload.get("additional_information")),
            _parse_provider_datetime(payload.get("created_on")),
            _to_json(payload),
            booking_id,
        ],
    )
    rooms = payload.get("booking_rooms")
    if rooms is not None:
        replace_booking_rooms(booking_id=booking_id, rooms=rooms)
    return booking


def replace_booking_rooms(*, booking_id: int, rooms: list[dict[str, Any]]) -> None:
    """Rewrite the room lines for a booking.

    Hotelios does not give a room line a stable identifier, so there is nothing
    to match on for an update — the whole set is replaced.
    """
    execute(f"DELETE FROM {HOTELIOS_BOOKING_ROOM_TABLE} WHERE booking_id = %s", [booking_id])
    for room in rooms:
        execute(
            f"""
            INSERT INTO {HOTELIOS_BOOKING_ROOM_TABLE} (
                booking_id, option_ref_id, room_type_id, room_type_name, rate_plan_id,
                meal_plan, included_meal_options, extra_bed_added,
                cancellation_policy, price, price_breakdown, guests, b2b_employee_id
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            [
                booking_id,
                room.get("option_ref_id"),
                room.get("room_type_id"),
                room.get("room_type_name"),
                room.get("rate_plan_id"),
                room.get("meal_plan"),
                _to_json(room.get("included_meal_options") or []),
                bool(room.get("extra_bed_added")),
                _to_json(room.get("cancellation_policy")),
                _parse_amount(room.get("price")),
                _to_json(room.get("price_breakdown")),
                _to_json(room.get("guests") or []),
                room.get("b2b_employee_id"),
            ],
        )


def set_booking_status(*, booking_id: int, status: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {HOTELIOS_BOOKING_TABLE}
        SET status = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [status, booking_id],
    )


def record_status_event(
    *,
    booking_id: int,
    status: str,
    previous_status: str | None = None,
    source: str = "api",
    payload: Any = None,
) -> None:
    execute(
        f"""
        INSERT INTO {HOTELIOS_BOOKING_EVENT_TABLE}
            (booking_id, previous_status, status, source, payload)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [booking_id, previous_status, status, source, _to_json(payload)],
    )


def fetch_booking(booking_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {HOTELIOS_BOOKING_TABLE} WHERE id = %s", [booking_id])


def fetch_booking_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {HOTELIOS_BOOKING_TABLE} WHERE guid = %s", [guid])


def fetch_booking_by_external_id(external_id: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {HOTELIOS_BOOKING_TABLE} WHERE external_id = %s", [external_id]
    )


def fetch_booking_rooms(booking_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {HOTELIOS_BOOKING_ROOM_TABLE} WHERE booking_id = %s ORDER BY id",
        [booking_id],
    )


def fetch_bookings_for_client(*, client_user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_BOOKING_TABLE}
        WHERE client_user_id = %s
        ORDER BY created_at DESC
        """,
        [client_user_id],
    )


def fetch_bookings_for_company(
    *,
    b2b_company_id: int,
    trip_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["b2b_company_id = %s"]
    params: list[Any] = [b2b_company_id]
    if trip_id is not None:
        conditions.append("b2b_trip_id = %s")
        params.append(trip_id)
    if status:
        conditions.append("status = %s")
        params.append(status)
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_BOOKING_TABLE}
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC
        """,
        params,
    )


def fetch_live_bookings(*, limit: int = 200) -> list[dict[str, Any]]:
    """Bookings sent to a hotel that have not settled into a final state."""
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_BOOKING_TABLE}
        WHERE status = __ANY_MARKER__(%s)
          AND provider_booking_id IS NOT NULL
        ORDER BY updated_at ASC
        LIMIT %s
        """,
        [["PENDING", "WAIT_LIST"], limit],
    )


def fetch_stale_drafts(*, older_than: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """Drafts that were created upstream but never confirmed.

    A quote only lives about an hour, and Hotelios asks that Create and Confirm
    stay close together. Anything still sitting in DRAFT well past that is
    abandoned — the person walked away from the payment screen.
    """
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_BOOKING_TABLE}
        WHERE status = 'DRAFT'
          AND provider_booking_id IS NOT NULL
          AND created_at < %s
        ORDER BY created_at ASC
        LIMIT %s
        """,
        [older_than, limit],
    )


def fetch_status_events(*, booking_id: int, limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {HOTELIOS_BOOKING_EVENT_TABLE}
        WHERE booking_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        [booking_id, limit],
    )
