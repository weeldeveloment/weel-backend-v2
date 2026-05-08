from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from core import settings
from payment.exchange_rate import to_uzs
from payment.raw_repository import (
    create_charge_transaction_from_latest,
    get_latest_transaction_history_for_booking,
    mark_latest_transaction_dismissed,
)
from payment.services import PlumAPIError, PlumAPIService
from property.apartment_repository import parse_property_kind
from users.raw_repository import get_user_by_id

from .guest_rules import extra_guest_fee_total, listing_included_guests
from .helpers import client_can_cancel, get_cancellation_error_message
from .raw_booking_repository import (
    get_booking_by_guid,
    get_verified_property_for_booking,
    release_calendar_for_booking,
    update_booking_status,
)
from .raw_calendar_service import RawCalendarDateService
from .raw_create_service import (
    RawBookingCreateService,
    _resolve_cottage_day_price,
)

logger = logging.getLogger(__name__)


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _as_namespace(row: dict[str, Any] | None):
    if not row:
        return None
    return SimpleNamespace(**row)


def _extract_property_guid(property_value: Any) -> str | None:
    if isinstance(property_value, dict):
        guid = property_value.get("guid")
    else:
        guid = getattr(property_value, "guid", None)
    return str(guid) if guid else None


def _infer_property_kind(property_value: Any) -> str | None:
    if isinstance(property_value, dict):
        direct = property_value.get("property_kind")
        if direct:
            return str(direct)
        ptype = property_value.get("property_type")
        if isinstance(ptype, str):
            return parse_property_kind(ptype)
        return None

    direct = getattr(property_value, "property_kind", None)
    if direct:
        return str(direct)

    property_type = getattr(property_value, "property_type", None)
    if property_type is None:
        return None
    for field in ("title_en", "title", "title_ru", "title_uz"):
        raw = getattr(property_type, field, None)
        if raw:
            kind = parse_property_kind(str(raw))
            if kind:
                return kind
    return None


def _extract_property_id(property_value: Any) -> int | None:
    if isinstance(property_value, dict):
        raw = property_value.get("property_id", property_value.get("id"))
    else:
        raw = getattr(property_value, "id", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _resolve_property_row(property_value: Any) -> dict[str, Any]:
    guid = _extract_property_guid(property_value)
    if guid:
        row = get_verified_property_for_booking(guid)
        if row:
            return row

    kind = _infer_property_kind(property_value)
    property_id = _extract_property_id(property_value)
    if not kind or property_id is None:
        raise ValidationError(_("Property context is invalid for raw booking flow"))

    title = ""
    currency = "UZS"
    if isinstance(property_value, dict):
        title = str(property_value.get("title") or "")
        currency = str(property_value.get("currency") or "UZS")
    else:
        title = str(getattr(property_value, "title", "") or "")
        currency = str(getattr(property_value, "currency", "UZS") or "UZS")

    return {
        "guid": guid,
        "property_kind": kind,
        "property_id": property_id,
        "partner_user_id": None,
        "title": title,
        "price": (
            property_value.get("price")
            if isinstance(property_value, dict)
            else getattr(property_value, "price", None)
        ),
        "price_on_working_days": (
            property_value.get("price_on_working_days")
            if isinstance(property_value, dict)
            else getattr(property_value, "price_on_working_days", None)
        ),
        "price_on_weekends": (
            property_value.get("price_on_weekends")
            if isinstance(property_value, dict)
            else getattr(property_value, "price_on_weekends", None)
        ),
        "price_per_person": (
            property_value.get("price_per_person")
            if isinstance(property_value, dict)
            else getattr(property_value, "price_per_person", None)
        ),
        "currency": currency,
        "weekend_only_sunday_inclusive": bool(
            property_value.get("weekend_only_sunday_inclusive")
            if isinstance(property_value, dict)
            else getattr(property_value, "weekend_only_sunday_inclusive", False)
        ),
        "guests": (
            property_value.get("guests")
            if isinstance(property_value, dict)
            else (
                getattr(getattr(property_value, "property_room", None), "guests", None)
                if getattr(property_value, "property_room", None) is not None
                else getattr(property_value, "guests", None)
            )
        ),
    }


def _resolve_booking_row(booking: Any) -> dict[str, Any]:
    if isinstance(booking, dict):
        if booking.get("id") and booking.get("guid"):
            return booking
        booking_guid = booking.get("guid")
        if booking_guid:
            row = get_booking_by_guid(str(booking_guid))
            if row:
                return row

    booking_guid = getattr(booking, "guid", None) if booking is not None else None
    if booking_guid:
        row = get_booking_by_guid(str(booking_guid))
        if row:
            return row

    if isinstance(booking, str):
        row = get_booking_by_guid(booking)
        if row:
            return row

    raise ValidationError(_("Booking not found"))


class CalendarDateService:
    """Compatibility service backed by raw SQL calendar layer."""

    def __init__(self, property, from_date, to_date):
        self.property = property
        self.from_date = from_date
        self.to_date = to_date

    def _raw(self) -> RawCalendarDateService:
        property_row = _resolve_property_row(self.property)
        return RawCalendarDateService(
            property_guid=property_row.get("guid"),
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["property_id"]),
            from_date=self.from_date,
            to_date=self.to_date,
        )

    def block(self):
        return self._raw().block()

    def unblock(self):
        return self._raw().unblock()

    def hold(self):
        return self._raw().hold()

    def unhold(self):
        return self._raw().unhold()


class BookingPriceService:
    def __init__(self):
        raw_service_fee = getattr(settings, "SERVICE_FEE", "20")
        try:
            self.server_fee = Decimal(str(raw_service_fee or "20"))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning("Invalid SERVICE_FEE=%r. Falling back to 20.", raw_service_fee)
            self.server_fee = Decimal("20")

    @staticmethod
    def _date_range(start: date, end: date):
        current = start
        while current < end:
            yield current
            current += timedelta(days=1)

    def calculate(
        self,
        adults: int,
        children: int,
        check_in: date,
        check_out: date,
        property,
    ):
        property_row = _resolve_property_row(property)
        guests = adults + children
        included_guests = listing_included_guests(property_row)
        extra_persons = max(guests - included_guests, 0)
        property_kind = property_row.get("property_kind")

        base_total_price = Decimal("0")

        if property_kind == "apartment":
            price = _to_decimal(property_row.get("price"))
            if price is None or price <= 0:
                raise ValidationError(_("Pricing is not configured for this property"))
            base_total_price = price
        else:
            price_rows = property_row.get("price")
            for day in self._date_range(check_in, check_out):
                if day.weekday() >= 4:
                    base_day = _to_decimal(property_row.get("price_on_weekends"))
                else:
                    base_day = _to_decimal(property_row.get("price_on_working_days"))
                if price_rows:
                    price_item = _resolve_cottage_day_price(price_rows, day)
                    if price_item:
                        if day.weekday() >= 4:
                            base_day = _to_decimal(price_item.get("price_on_weekends"))
                        else:
                            base_day = _to_decimal(price_item.get("price_on_working_days"))
                base_total_price += base_day

        extra_total_uzs = extra_guest_fee_total(guests, property_row)
        currency = str(property_row.get("currency") or "UZS").upper()
        if currency == "USD":
            subtotal = to_uzs(base_total_price) + extra_total_uzs
        elif currency == "UZS":
            subtotal = base_total_price + extra_total_uzs
        else:
            raise ValidationError(_("Unsupported currency"))

        service_fee = subtotal * self.server_fee / Decimal("100")
        hold_amount = service_fee
        charge_amount = service_fee * Decimal("0.50")

        return {
            "nights": (check_out - check_in).days,
            "guests": guests,
            "included_guests": included_guests,
            "extra_persons": extra_persons,
            "subtotal": subtotal,
            "hold_amount": hold_amount,
            "charge_amount": charge_amount,
            "service_fee": service_fee,
            "service_fee_percentage": int(self.server_fee),
        }


class BookingService:
    """Compatibility layer that uses raw SQL booking repositories."""

    def __init__(self, client, property):
        self.client = client
        self.property = property
        self.booking_price_service = BookingPriceService()
        self.plum_service = PlumAPIService()

    def _client_raw(self):
        client_id = getattr(self.client, "id", None)
        if client_id is None and isinstance(self.client, dict):
            client_id = self.client.get("id")
        if client_id is None:
            raise ValidationError(_("Client context is invalid"))
        raw_user = get_user_by_id(int(client_id), role="client")
        if raw_user:
            return raw_user
        return SimpleNamespace(
            id=int(client_id),
            first_name=getattr(self.client, "first_name", "") if not isinstance(self.client, dict) else self.client.get("first_name", ""),
            last_name=getattr(self.client, "last_name", "") if not isinstance(self.client, dict) else self.client.get("last_name", ""),
        )

    def create_booking(self, check_in: date, check_out: date, data):
        property_row = _resolve_property_row(self.property)
        raw_service = RawBookingCreateService(client=self._client_raw())
        booking, hold = raw_service.create_booking(
            property_row=property_row,
            check_in=check_in,
            check_out=check_out,
            card_id=data["card_id"],
            adults=int(data.get("adults", 1)),
            children=int(data.get("children", 0)),
            babies=int(data.get("babies", 0)),
        )
        return _as_namespace(booking), hold

    def cancel_booking(self, booking, notify_partner: bool = True):
        booking_row = _resolve_booking_row(booking)
        booking_obj = SimpleNamespace(
            status=booking_row["status"],
            check_in=booking_row["check_in"],
            created_at=booking_row["created_at"],
        )
        if not client_can_cancel(booking_obj):
            raise ValidationError(get_cancellation_error_message(booking_obj))

        if booking_row["status"] == "pending":
            tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
            if tx and tx.get("transaction_id") and tx.get("hold_id"):
                try:
                    self.plum_service.dismiss_hold(
                        transaction_id=tx["transaction_id"],
                        hold_id=tx["hold_id"],
                    )
                except PlumAPIError as plum_api_error:
                    if plum_api_error.status_code == 403:
                        raise PermissionDenied(plum_api_error.message)
                    raise ValidationError(plum_api_error.message)
            mark_latest_transaction_dismissed(int(booking_row["id"]))

        release_calendar_for_booking(booking_row)
        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="cancelled",
            cancellation_reason="user_cancelled",
            set_cancelled=True,
        )
        return _as_namespace(updated or booking_row)

    def partner_accept(self, booking, notify_partner: bool = True):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "pending":
            raise ValidationError(_("You can only accept bookings with **pending** statuses"))

        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="confirmed",
            set_confirmed=True,
        )
        return _as_namespace(updated or booking_row)

    def partner_cancel(self, booking, notify_partner: bool = True):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "pending":
            raise ValidationError(_("Partner can cancel only bookings with status `PENDING`"))

        tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
        if tx and tx.get("transaction_id") and tx.get("hold_id"):
            try:
                self.plum_service.dismiss_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                )
            except PlumAPIError as plum_api_error:
                if plum_api_error.status_code == 403:
                    raise PermissionDenied(plum_api_error.message)
                raise ValidationError(plum_api_error.message)
        mark_latest_transaction_dismissed(int(booking_row["id"]))

        release_calendar_for_booking(booking_row)
        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="cancelled",
            cancellation_reason="partner_cancelled",
            set_cancelled=True,
        )
        return _as_namespace(updated or booking_row)

    def system_cancel_booking(self, booking):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "pending":
            return _as_namespace(booking_row)

        tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
        if tx and tx.get("transaction_id") and tx.get("hold_id"):
            try:
                self.plum_service.dismiss_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                )
            except Exception:
                logger.exception(
                    "system_cancel_booking: dismiss_hold failed",
                    extra={"booking_id": str(booking_row.get("guid"))},
                )
        mark_latest_transaction_dismissed(int(booking_row["id"]))

        release_calendar_for_booking(booking_row)
        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="cancelled",
            cancellation_reason="system_timeout",
            set_cancelled=True,
        )
        return _as_namespace(updated or booking_row)

    def system_complete_booking(self, booking):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "confirmed":
            return _as_namespace(booking_row)

        tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
        charge_amount = booking_row.get("booking_charge_amount")
        if tx and tx.get("transaction_id") and tx.get("hold_id") and charge_amount:
            try:
                charge_transaction = self.plum_service.charge_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                    charge_amount=charge_amount,
                )
                result = (charge_transaction or {}).get("result") or {}
                create_charge_transaction_from_latest(
                    booking_row=booking_row,
                    transaction_id=result.get("transactionId") or tx.get("transaction_id"),
                    hold_id=result.get("holdId") or tx.get("hold_id"),
                    amount=result.get("amount") or charge_amount,
                    card_id=result.get("cardId") or tx.get("card_id"),
                    extra_id=result.get("extraId"),
                )
            except Exception:
                logger.exception(
                    "system_complete_booking: charge_hold failed",
                    extra={"booking_id": str(booking_row.get("guid"))},
                )

        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="completed",
            set_completed=True,
        )
        return _as_namespace(updated or booking_row)

    def complete_booking(self, booking, notify_partner: bool = True):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "confirmed":
            raise ValidationError(_("Only confirmed bookings can be completed"))

        tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
        if not tx or not tx.get("transaction_id") or not tx.get("hold_id"):
            raise ValidationError(_("Payment transaction not found"))

        charge_amount = booking_row.get("booking_charge_amount")
        try:
            charge_transaction = self.plum_service.charge_hold(
                transaction_id=tx["transaction_id"],
                hold_id=tx["hold_id"],
                charge_amount=charge_amount,
            )
            result = (charge_transaction or {}).get("result") or {}
            create_charge_transaction_from_latest(
                booking_row=booking_row,
                transaction_id=result.get("transactionId") or tx.get("transaction_id"),
                hold_id=result.get("holdId") or tx.get("hold_id"),
                amount=result.get("amount") or charge_amount,
                card_id=result.get("cardId") or tx.get("card_id"),
                extra_id=result.get("extraId"),
            )
        except PlumAPIError as plum_api_error:
            if plum_api_error.status_code == 403:
                raise PermissionDenied(plum_api_error.message)
            raise ValidationError(plum_api_error.message)

        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="completed",
            set_completed=True,
        )
        return _as_namespace(updated or booking_row)

    def mark_no_show(self, booking, notify_partner: bool = True):
        booking_row = _resolve_booking_row(booking)
        if booking_row["status"] != "confirmed":
            raise ValidationError(_("Only confirmed bookings can be marked as no-show"))

        tx = get_latest_transaction_history_for_booking(int(booking_row["id"]))
        hold_amount = booking_row.get("booking_hold_amount")
        if tx and tx.get("transaction_id") and tx.get("hold_id") and hold_amount:
            try:
                charge_transaction = self.plum_service.charge_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                    charge_amount=hold_amount,
                )
                result = (charge_transaction or {}).get("result") or {}
                create_charge_transaction_from_latest(
                    booking_row=booking_row,
                    transaction_id=result.get("transactionId") or tx.get("transaction_id"),
                    hold_id=result.get("holdId") or tx.get("hold_id"),
                    amount=result.get("amount") or hold_amount,
                    card_id=result.get("cardId") or tx.get("card_id"),
                    extra_id=result.get("extraId"),
                )
            except PlumAPIError as plum_api_error:
                raise ValidationError(plum_api_error.message)

        updated = update_booking_status(
            booking_id=int(booking_row["id"]),
            status="cancelled",
            cancellation_reason="user_no_show",
            set_cancelled=True,
        )
        return _as_namespace(updated or booking_row)
