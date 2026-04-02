from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import DecimalField, Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from shared.date import month_end, month_start

from .models import Property, PropertyPrice


WEEKEND_START_WEEKDAY = 4  # Friday


def resolve_reference_date(raw_from_date: str | None, default_date: date | None = None) -> date:
    if default_date is None:
        default_date = timezone.localdate()
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


def related_prices(property_obj: Property):
    return property_obj.property_price.all().order_by("month_from", "created_at")


def get_effective_price_row(
    property_obj: Property,
    reference_date: date | None = None,
) -> PropertyPrice | None:
    reference_date = reference_date or timezone.localdate()
    prices = related_prices(property_obj)
    covering = prices.filter(
        month_from__lte=reference_date,
        month_to__gte=reference_date,
    ).first()
    if covering:
        return covering
    return prices.first()


def get_effective_price_amount(
    property_obj: Property,
    reference_date: date | None = None,
) -> Decimal | None:
    reference_date = reference_date or timezone.localdate()
    price_row = get_effective_price_row(property_obj, reference_date)
    if price_row is None:
        return None
    field_name = price_field_for_date(reference_date)
    return getattr(price_row, field_name)


def upsert_uniform_monthly_prices(
    property_obj: Property,
    base_price: Decimal,
    *,
    include_next_month: bool = True,
    reference_date: date | None = None,
) -> list[PropertyPrice]:
    reference_date = reference_date or timezone.localdate()
    base_price = Decimal(str(base_price))

    month_starts = [month_start(reference_date)]
    if include_next_month:
        month_starts.append(month_start(reference_date + relativedelta(months=1)))

    rows: list[PropertyPrice] = []
    for current_month_start in month_starts:
        row, _ = PropertyPrice.objects.update_or_create(
            property=property_obj,
            month_from=current_month_start,
            defaults={
                "month_to": month_end(current_month_start),
                "price_per_person": Decimal("0"),
                "price_on_working_days": base_price,
                "price_on_weekends": base_price,
            },
        )
        rows.append(row)
    return rows


def property_price_expression(price_field: str, reference_date: date):
    return Coalesce(
        Min(
            price_field,
            filter=Q(
                property_price__month_from__lte=reference_date,
                property_price__month_to__gte=reference_date,
            ),
        ),
        Min(price_field),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


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
