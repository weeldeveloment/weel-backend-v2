"""Shared SQL fragment builders for the public search / map filters.

Apartments and cottages live in separate tables with different aliases but the
same filter surface, so the WHERE fragments are built here once and reused by
both repositories.
"""

from __future__ import annotations

from typing import Any

from shared.raw.compat import is_postgresql


SERVICES_MATCH_ALL = "all"
SERVICES_MATCH_ANY = "any"


def numeric_column_sql(alias: str, column: str) -> str:
    """Read a room/bed/guest count as an integer.

    These columns are text on some deployments and integer on others, so the
    digits are extracted before casting instead of relying on the column type.
    """
    return (
        f"COALESCE(NULLIF(regexp_replace(COALESCE({alias}.{column}::text, ''), '[^0-9]', '', 'g'), ''), '0')::int"
    )


def append_services_filter(
    where: list[str],
    params: list[Any],
    *,
    alias: str,
    services: list[str] | None,
    match: str = SERVICES_MATCH_ALL,
) -> None:
    """Filter by amenity GUIDs stored in the `services` uuid[] column."""
    if not services or not is_postgresql():
        return
    values = [str(service).strip() for service in services if str(service or "").strip()]
    if not values:
        return
    operator = "&&" if match == SERVICES_MATCH_ANY else "@>"
    where.append(f"COALESCE({alias}.services, ARRAY[]::uuid[]) {operator} %s::uuid[]")
    params.append(values)


def append_bbox_filter(
    where: list[str],
    params: list[Any],
    *,
    alias: str,
    bbox: tuple[float, float, float, float] | None,
) -> None:
    """Filter to a map viewport given as (sw_lat, sw_lon, ne_lat, ne_lon)."""
    if not bbox:
        return
    sw_lat, sw_lon, ne_lat, ne_lon = bbox
    lat_min, lat_max = sorted((float(sw_lat), float(ne_lat)))
    clause = (
        f"({alias}.latitude IS NOT NULL AND {alias}.longitude IS NOT NULL"
        f" AND {alias}.latitude::float BETWEEN %s AND %s"
    )
    params.extend([lat_min, lat_max])
    if float(sw_lon) <= float(ne_lon):
        clause += f" AND {alias}.longitude::float BETWEEN %s AND %s)"
        params.extend([float(sw_lon), float(ne_lon)])
    else:
        # Viewport crosses the antimeridian.
        clause += (
            f" AND ({alias}.longitude::float >= %s OR {alias}.longitude::float <= %s))"
        )
        params.extend([float(sw_lon), float(ne_lon)])
    where.append(clause)


def append_capacity_filters(
    where: list[str],
    params: list[Any],
    *,
    alias: str,
    min_guests: int | None = None,
    min_rooms: int | None = None,
    min_beds: int | None = None,
    min_bathrooms: int | None = None,
    available_columns: dict[str, bool] | None = None,
) -> None:
    """Apply the "Комнаты и кровати" minimum-count filters."""
    wanted = {
        "guests": min_guests,
        "rooms": min_rooms,
        "beds": min_beds,
        "bathrooms": min_bathrooms,
    }
    for column, value in wanted.items():
        if value is None or int(value) <= 0:
            continue
        if available_columns is not None and not available_columns.get(column, True):
            continue
        where.append(f"{numeric_column_sql(alias, column)} >= %s")
        params.append(int(value))


def append_flag_filters(
    where: list[str],
    params: list[Any],
    *,
    alias: str,
    allowed_pets: bool | None = None,
    allowed_alcohol: bool | None = None,
) -> None:
    for column, value in (
        ("is_allowed_pets", allowed_pets),
        ("is_allowed_alcohol", allowed_alcohol),
    ):
        if value is None:
            continue
        where.append(f"COALESCE({alias}.{column}, FALSE) = %s")
        params.append(bool(value))
