from __future__ import annotations

from datetime import date, timedelta

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import ValidationError

from .raw_repository import (
    delete_calendar_days_by_status,
    fetch_calendar_dates_by_status,
    upsert_calendar_days,
)


class RawCalendarStatus:
    AVAILABLE = "available"
    BOOKED = "booked"
    BLOCKED = "blocked"
    HELD = "held"


class RawCalendarDateService:
    HOLD_TTL_SECONDS = 60 * 30

    def __init__(
        self,
        *,
        property_guid,
        property_kind: str,
        property_id: int,
        from_date: date,
        to_date: date,
    ):
        self.property_guid = property_guid
        self.property_kind = property_kind
        self.property_id = int(property_id)
        self.from_date = from_date
        self.to_date = to_date

    def _build_days(self) -> list[date]:
        days: list[date] = []
        current = self.from_date
        while current <= self.to_date:
            days.append(current)
            current += timedelta(days=1)
        return days

    def _cache_key(self, day: date) -> str:
        return f"calendar:hold:{self.property_guid}:{day.isoformat()}"

    def _validate_booked_dates(self):
        booked_dates = fetch_calendar_dates_by_status(
            property_kind=self.property_kind,
            property_id=self.property_id,
            from_date=self.from_date,
            to_date=self.to_date,
            statuses=[RawCalendarStatus.BOOKED],
        )
        if booked_dates:
            booked_dates_str = ", ".join(booked.isoformat() for booked in booked_dates)
            raise ValidationError(
                _(
                    "Some dates are booked and can't be modified: {booked_dates}".format(
                        booked_dates=booked_dates_str
                    )
                )
            )

    def _validate_held_days(self):
        held_dates: list[date] = []
        for day in self._build_days():
            if cache.get(self._cache_key(day)):
                held_dates.append(day)
        if held_dates:
            held_dates_str = ", ".join(day.isoformat() for day in held_dates)
            raise ValidationError(
                _(
                    "Some dates are temporarily held by partners and can't be blocked: {held_dates}"
                ).format(held_dates=held_dates_str)
            )

    def block(self) -> list[date]:
        self._validate_booked_dates()
        self._validate_held_days()

        existing_blocked_dates = fetch_calendar_dates_by_status(
            property_kind=self.property_kind,
            property_id=self.property_id,
            from_date=self.from_date,
            to_date=self.to_date,
            statuses=[RawCalendarStatus.BLOCKED],
        )
        if existing_blocked_dates:
            existing_blocked_dates_str = ", ".join(
                blocked.isoformat() for blocked in existing_blocked_dates
            )
            raise ValidationError(
                {
                    "detail": _(
                        "Some dates are already blocked: {existing_blocked_dates}"
                    ).format(existing_blocked_dates=existing_blocked_dates_str)
                }
            )

        days = self._build_days()
        upsert_calendar_days(
            property_kind=self.property_kind,
            property_id=self.property_id,
            days=days,
            status=RawCalendarStatus.BLOCKED,
        )
        return days

    def unblock(self) -> list[date]:
        self._validate_booked_dates()
        blocked_dates = fetch_calendar_dates_by_status(
            property_kind=self.property_kind,
            property_id=self.property_id,
            from_date=self.from_date,
            to_date=self.to_date,
            statuses=[RawCalendarStatus.BLOCKED],
        )
        if not blocked_dates:
            raise ValidationError(_("No blocked dates were found in the specified range"))

        delete_calendar_days_by_status(
            property_kind=self.property_kind,
            property_id=self.property_id,
            from_date=self.from_date,
            to_date=self.to_date,
            status=RawCalendarStatus.BLOCKED,
        )
        return blocked_dates

    def hold(self) -> list[date]:
        self._validate_booked_dates()

        existing_blocked_dates = fetch_calendar_dates_by_status(
            property_kind=self.property_kind,
            property_id=self.property_id,
            from_date=self.from_date,
            to_date=self.to_date,
            statuses=[RawCalendarStatus.BLOCKED],
        )
        if existing_blocked_dates:
            existing_blocked_dates_str = ", ".join(
                blocked.isoformat() for blocked in existing_blocked_dates
            )
            raise ValidationError(
                {
                    "detail": _(
                        "Some dates are already blocked: {existing_blocked_dates}"
                    ).format(existing_blocked_dates=existing_blocked_dates_str)
                }
            )

        days = self._build_days()
        held_days = [day for day in days if cache.get(self._cache_key(day))]
        if held_days:
            held_days_str = ", ".join(held_day.isoformat() for held_day in held_days)
            raise ValidationError(
                _("Some dates are temporarily held: {held_days}").format(
                    held_days=held_days_str
                )
            )

        for day in days:
            cache.set(self._cache_key(day), True, timeout=self.HOLD_TTL_SECONDS)
        return days

    def unhold(self) -> list[date]:
        self._validate_booked_dates()

        removed: list[date] = []
        for day in self._build_days():
            cache_key = self._cache_key(day)
            if cache.get(cache_key):
                cache.delete(cache_key)
                removed.append(day)
        if not removed:
            raise ValidationError(_("No held dates were found in the specified range"))
        return removed
