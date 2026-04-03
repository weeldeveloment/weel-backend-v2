from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from django.utils import timezone

from shared.date import month_end, month_start
from shared.raw.db import execute, fetch_one
from shared.raw.tables import PROPERTY_PRICE_TABLE


WEEKEND_START_WEEKDAY = 4


@dataclass(slots=True)
class RawPropertyPriceRow:
    id: int | None = None
    guid: str | None = None
    property_id: int | None = None
    month_from: date | None = None
    month_to: date | None = None
    price_per_person: Decimal | None = None
    price_on_working_days: Decimal | None = None
    price_on_weekends: Decimal | None = None


def resolve_reference_date(raw_from_date: str | None, default_date: date | None = None) -> date:
    default_date = default_date or timezone.localdate()
    if not raw_from_date:
        return default_date
    try:
        return date.fromisoformat(str(raw_from_date).strip())
    except (TypeError, ValueError):
        return default_date


def is_weekend(day: date) -> bool:
    return day.weekday() >= WEEKEND_START_WEEKDAY


def price_field_for_date(day: date) -> str:
    return "price_on_weekends" if is_weekend(day) else "price_on_working_days"


def related_prices(property_obj):
    property_id = getattr(property_obj, "id", None)
    if property_id is None:
        return []
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM {PROPERTY_PRICE_TABLE}
        WHERE property_id = %s
        """,
        [property_id],
    )
    return [] if row is None else [int(row.get("total") or 0)]


def get_effective_price_row(property_obj, reference_date: date | None = None) -> RawPropertyPriceRow | None:
    property_id = getattr(property_obj, "id", None)
    if property_id is None:
        return None

    reference_date = reference_date or timezone.localdate()
    covering = fetch_one(
        f"""
        SELECT *
        FROM {PROPERTY_PRICE_TABLE}
        WHERE property_id = %s
          AND month_from <= %s
          AND month_to >= %s
        ORDER BY month_from ASC
        LIMIT 1
        """,
        [property_id, reference_date, reference_date],
    )
    if covering:
        return RawPropertyPriceRow(**covering)

    first_price = fetch_one(
        f"""
        SELECT *
        FROM {PROPERTY_PRICE_TABLE}
        WHERE property_id = %s
        ORDER BY month_from ASC
        LIMIT 1
        """,
        [property_id],
    )
    return RawPropertyPriceRow(**first_price) if first_price else None


def get_effective_price_amount(property_obj, reference_date: date | None = None) -> Decimal | None:
    reference_date = reference_date or timezone.localdate()
    price_row = get_effective_price_row(property_obj, reference_date)
    if price_row is None:
        return None
    return getattr(price_row, price_field_for_date(reference_date))


def upsert_uniform_monthly_prices(property_obj, base_price: Decimal, *, include_next_month: bool = True, reference_date: date | None = None) -> list[RawPropertyPriceRow]:
    property_id = getattr(property_obj, "id", None)
    if property_id is None:
        return []

    reference_date = reference_date or timezone.localdate()
    base_price = Decimal(str(base_price))
    month_starts = [month_start(reference_date)]
    if include_next_month:
        month_starts.append(month_start(reference_date + relativedelta(months=1)))

    rows: list[RawPropertyPriceRow] = []
    now = timezone.now()
    for current_month_start in month_starts:
        current_month_end = month_end(current_month_start)
        existing = fetch_one(
            f"""
            SELECT id, guid
            FROM {PROPERTY_PRICE_TABLE}
            WHERE property_id = %s
              AND month_from = %s
            LIMIT 1
            """,
            [property_id, current_month_start],
        )
        if existing:
            execute(
                f"""
                UPDATE {PROPERTY_PRICE_TABLE}
                SET month_to = %s,
                    price_per_person = %s,
                    price_on_working_days = %s,
                    price_on_weekends = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                [current_month_end, Decimal("0"), base_price, base_price, now, existing["id"]],
            )
            rows.append(
                RawPropertyPriceRow(
                    id=existing["id"],
                    guid=existing["guid"],
                    property_id=property_id,
                    month_from=current_month_start,
                    month_to=current_month_end,
                    price_per_person=Decimal("0"),
                    price_on_working_days=base_price,
                    price_on_weekends=base_price,
                )
            )
            continue

        inserted = fetch_one(
            f"""
            INSERT INTO {PROPERTY_PRICE_TABLE} (
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
            RETURNING id, guid
            """,
            [uuid4(), now, now, property_id, current_month_start, current_month_end, Decimal("0"), base_price, base_price],
        )
        if inserted:
            rows.append(
                RawPropertyPriceRow(
                    id=inserted["id"],
                    guid=inserted["guid"],
                    property_id=property_id,
                    month_from=current_month_start,
                    month_to=current_month_end,
                    price_per_person=Decimal("0"),
                    price_on_working_days=base_price,
                    price_on_weekends=base_price,
                )
            )
    return rows


def property_price_expression_sql(price_field: str, reference_date: date) -> str:
    return f"""
    COALESCE(
        MIN(CASE
            WHEN month_from <= '{reference_date}' AND month_to >= '{reference_date}'
            THEN {price_field}
            ELSE NULL
        END),
        MIN({price_field})
    )
    """


def day_type_flags(from_date: date, to_date: date) -> tuple[bool, bool]:
    has_weekdays = False
    has_weekends = False
    current = from_date
    while current <= to_date:
        if is_weekend(current):
            has_weekends = True
        else:
            has_weekdays = True
        current += timedelta(days=1)
    return has_weekdays, has_weekends
