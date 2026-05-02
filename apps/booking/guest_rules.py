"""Guest count limits and extra-guest pricing for bookings."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings


def booking_max_guests() -> int:
    return int(getattr(settings, "BOOKING_MAX_GUESTS", 6) or 6)


def extra_guest_fee_uzs() -> Decimal:
    raw = getattr(settings, "BOOKING_EXTRA_GUEST_FEE_UZS", "100000")
    try:
        return Decimal(str(raw))
    except Exception:
        return Decimal("100000")


def listing_included_guests(property_row: dict[str, Any] | None) -> int:
    """Partner-configured standard occupancy, capped by platform max."""
    if not property_row:
        return booking_max_guests()
    try:
        raw = int(property_row.get("guests") or 0)
    except (TypeError, ValueError):
        raw = 0
    cap = booking_max_guests()
    if raw <= 0:
        return cap
    return min(raw, cap)


def extra_guest_count(guests: int, property_row: dict[str, Any] | None) -> int:
    return max(0, int(guests) - listing_included_guests(property_row))


def extra_guest_fee_total(guests: int, property_row: dict[str, Any] | None) -> Decimal:
    return extra_guest_fee_uzs() * extra_guest_count(guests, property_row)
