from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import PermissionDenied, ValidationError

from core import settings
from notification.service import NotificationService
from payment.exchange_rate import to_uzs
from payment.raw_repository import create_hold_transaction
from payment.services import PlumAPIError, PlumAPIService
from shared.raw.entities import RawUser
from users.tasks import send_partner_telegram_msg

from .raw_booking_repository import (
    create_booking_row,
)
from .raw_calendar_service import RawCalendarStatus
from .raw_repository import fetch_calendar_dates_by_status, upsert_calendar_days


logger = logging.getLogger(__name__)


class RawBookingCreateService:
    def __init__(self, client: RawUser):
        self.client = client
        self.plum_service = PlumAPIService()

        raw_service_fee = getattr(settings, "SERVICE_FEE", "20")
        try:
            self.service_fee_percentage = Decimal(str(raw_service_fee or "20"))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning(
                "Invalid SERVICE_FEE=%r. Falling back to 20.", raw_service_fee
            )
            self.service_fee_percentage = Decimal("20")

    @staticmethod
    def _date_range(check_in: date, check_out: date):
        current = check_in
        while current < check_out:
            yield current
            current += timedelta(days=1)

    @staticmethod
    def _hold_cache_key(property_guid, day: date) -> str:
        return f"calendar:hold:{property_guid}:{day.isoformat()}"

    @staticmethod
    def _as_decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    @staticmethod
    def _generate_booking_number() -> str:
        return "".join(str(secrets.randbelow(10)) for _ in range(7))

    def _validate_dates_availability(
        self,
        *,
        property_guid,
        property_kind: str,
        property_id: int,
        check_in: date,
        check_out: date,
    ) -> list[date]:
        date_from = check_in
        date_to = check_out - timedelta(days=1)

        unavailable_days = fetch_calendar_dates_by_status(
            property_kind=property_kind,
            property_id=property_id,
            from_date=date_from,
            to_date=date_to,
            statuses=[RawCalendarStatus.BOOKED, RawCalendarStatus.BLOCKED],
        )
        if unavailable_days:
            unavailable_days_str = ", ".join(day.isoformat() for day in unavailable_days)
            raise ValidationError(
                _(
                    "This property is temporarily reserved for these dates. "
                    "The first guest has 30 minutes to complete payment. "
                    "If they do not pay in time, the property will become available again. "
                    "Unavailable dates: {unavailable_days}"
                ).format(unavailable_days=unavailable_days_str)
            )

        days = list(self._date_range(check_in, check_out))
        held_days = [
            day
            for day in days
            if cache.get(self._hold_cache_key(property_guid=property_guid, day=day))
        ]
        if held_days:
            held_days_str = ", ".join(day.isoformat() for day in held_days)
            raise ValidationError(
                _(
                    "This property is temporarily reserved for these dates. "
                    "The first guest has 30 minutes to complete payment. "
                    "If they do not pay in time, the property will become available again. "
                    "Reserved dates: {held_days}"
                ).format(held_days=held_days_str)
            )

        return days

    def _calculate_price(
        self,
        *,
        property_row: dict[str, Any],
        adults: int,
        children: int,
        check_in: date,
        check_out: date,
    ) -> dict[str, Any]:
        guests = adults + children
        property_kind = property_row.get("property_kind")

        base_total_price = Decimal("0")
        if property_kind == "apartment":
            apartment_price = property_row.get("price")
            if apartment_price is None:
                raise ValidationError(_("Pricing is not configured for this property"))
            base_total_price = self._as_decimal(apartment_price)
            if base_total_price <= 0:
                raise ValidationError(_("Pricing is not configured for this property"))
        else:
            for day in self._date_range(check_in, check_out):
                base_day_value = (
                    property_row.get("price_on_weekends")
                    if day.weekday() >= 4
                    else property_row.get("price_on_working_days")
                )
                if base_day_value is None:
                    raise ValidationError(_("Pricing is not configured for this property"))
                base_total_price += self._as_decimal(base_day_value)

        currency = str(property_row.get("currency") or "UZS").upper()
        if currency == "USD":
            subtotal = to_uzs(base_total_price)
        elif currency == "UZS":
            subtotal = base_total_price
        else:
            raise ValidationError(_("Unsupported currency"))

        service_fee = subtotal * self.service_fee_percentage / Decimal("100")
        hold_amount = service_fee
        charge_amount = service_fee * Decimal("0.50")

        return {
            "nights": (check_out - check_in).days,
            "guests": guests,
            "subtotal": subtotal,
            "hold_amount": hold_amount,
            "charge_amount": charge_amount,
            "service_fee": service_fee,
            "service_fee_percentage": int(self.service_fee_percentage),
        }

    def _insert_booking_with_retry(
        self,
        *,
        property_kind: str,
        property_id: int,
        check_in: date,
        check_out: date,
        adults: int,
        children: int,
        babies: int,
    ) -> dict[str, Any]:
        for _ in range(20):
            try:
                row = create_booking_row(
                    client_user_id=int(self.client.id),
                    property_kind=property_kind,
                    property_id=property_id,
                    check_in=check_in,
                    check_out=check_out,
                    adults=adults,
                    children=children,
                    babies=babies,
                    booking_number=self._generate_booking_number(),
                )
            except IntegrityError:
                continue
            if row:
                return row
        raise ValidationError(_("Unable to create booking. Please try again."))

    @transaction.atomic
    def create_booking(
        self,
        *,
        property_row: dict[str, Any],
        check_in: date,
        check_out: date,
        card_id: str,
        adults: int,
        children: int,
        babies: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        property_kind = str(property_row["property_kind"])
        property_id = int(property_row["property_id"])

        days = self._validate_dates_availability(
            property_guid=property_row["guid"],
            property_kind=property_kind,
            property_id=property_id,
            check_in=check_in,
            check_out=check_out,
        )
        booking_price = self._calculate_price(
            property_row=property_row,
            adults=adults,
            children=children,
            check_in=check_in,
            check_out=check_out,
        )

        try:
            hold = self.plum_service.create_hold(
                client=self.client,
                card_id=card_id,
                amount=booking_price["hold_amount"],
            )
        except PlumAPIError as plum_api_error:
            if plum_api_error.status_code == 403:
                raise PermissionDenied(plum_api_error.message)
            raise ValidationError(plum_api_error.message)

        hold_result = (hold or {}).get("result") or {}
        hold_amount = hold_result.get("totalAmount") or booking_price["hold_amount"]

        booking = self._insert_booking_with_retry(
            property_kind=property_kind,
            property_id=property_id,
            check_in=check_in,
            check_out=check_out,
            adults=adults,
            children=children,
            babies=babies,
        )

        create_hold_transaction(
            booking_id=int(booking["id"]),
            client_user_id=int(self.client.id),
            partner_user_id=int(property_row["partner_user_id"]),
            amount=hold_amount,
            transaction_id=hold_result.get("transactionId"),
            hold_id=hold_result.get("holdId"),
            card_id=hold_result.get("cardId") or card_id,
            extra_id=hold_result.get("extraId"),
        )

        upsert_calendar_days(
            property_kind=property_kind,
            property_id=property_id,
            days=days,
            status=RawCalendarStatus.BOOKED,
        )

        NotificationService.send_to_client(
            client=SimpleNamespace(id=int(self.client.id)),
            title="Booking created",
            message=(
                f"We have placed a hold of {booking_price['hold_amount']} UZS for your booking. "
                f"You have 30 minutes to complete payment; otherwise the property will be released "
                f"and available for others. The host will confirm your booking soon."
            ),
            notification_type="system",
            data={"booking_id": str(booking["guid"])},
        )
        NotificationService.send_to_partner(
            partner=SimpleNamespace(id=int(property_row["partner_user_id"])),
            title="New booking request",
            message=(
                f"You have a new booking request for {property_row['title']}. "
                f"Please review and confirm it in time."
            ),
            notification_type="system",
            data={
                "type": "booking_event",
                "event": "booking_requested",
                "booking_id": str(booking["guid"]),
                "booking_number": booking.get("booking_number"),
                "status": booking.get("status"),
                "property_title": property_row.get("title"),
                "check_in": str(check_in),
                "check_out": str(check_out),
                "client_name": f"{self.client.first_name or ''} {self.client.last_name or ''}".strip(),
            },
        )

        from .tasks import auto_cancel_booking

        auto_cancel_booking.apply_async(
            kwargs={"booking_id": str(booking["guid"])},
            countdown=60 * 30,
        )
        send_partner_telegram_msg.delay(
            partner_id=int(property_row["partner_user_id"]),
            message=(
                f"Yangi bron! {property_row['title']} -- {check_in} - {check_out}\n"
                f"Summa:{booking_price['subtotal'] * Decimal('0.9')}\n"
                f"Mijozni yo‘qotmaslik uchun bronni hozir tasdiqlang"
            ),
        )

        return booking, hold
