import logging

from datetime import timedelta, date, datetime
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError, PermissionDenied

from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from core import settings
from .helpers import client_can_cancel, get_cancellation_error_message
from .models import CalendarDate, Booking, BookingPrice, BookingTransaction
from property.models import Property, PropertyPrice
from users.models.clients import Client
from payment.models import PlumTransaction, PlumTransactionStatus
from payment.services import PlumAPIService, PlumAPIError
from payment.exchange_rate import to_uzs, to_usd
from notification.models import Notification
from notification.service import NotificationService

from users.tasks import send_partner_telegram_msg

logger = logging.getLogger(__name__)


class CalendarDateService:
    """Handles blocking and unblocking calendar dates"""

    HOLD_TTL_SECONDS = 60 * 30

    def __init__(self, property, from_date, to_date):
        self.property = property
        self.from_date = from_date
        self.to_date = to_date

    def _validate_booked_dates(self):
        booked_dates = list(
            CalendarDate.objects.filter(
                property=self.property,
                date__range=(self.from_date, self.to_date),
                status=CalendarDate.CalendarStatus.BOOKED,
            ).values_list("date", flat=True)
        )

        if booked_dates:
            booked_dates_str = ", ".join(
                booked_date.isoformat() for booked_date in booked_dates
            )
            raise ValidationError(
                _(
                    "Some dates are booked and can't be modified: {booked_dates}".format(
                        booked_dates=booked_dates_str
                    )
                )
            )

    def _validate_held_days(self):
        held_dates = []

        current_date = self.from_date
        while current_date <= self.to_date:
            cache_key = self._cache_key(day=current_date)
            if cache.get(cache_key):
                held_dates.append(current_date)
            current_date += timedelta(days=1)

        if held_dates:
            held_dates_str = ", ".join(
                held_date.isoformat() for held_date in held_dates
            )
            raise ValidationError(
                _(
                    "Some dates are temporarily held by partners and can't be blocked: {held_dates}"
                ).format(held_dates=held_dates_str)
            )

    def _build_days(self):
        days = []
        current = self.from_date
        while current <= self.to_date:
            days.append(current)
            current += timedelta(days=1)
        return days

    def _cache_key(self, day):
        return f"calendar:hold:{self.property.guid}:{day.isoformat()}"

    @transaction.atomic
    def block(self):
        self._validate_booked_dates()
        self._validate_held_days()

        existing_blocked_dates = set(
            CalendarDate.objects.filter(
                property=self.property,
                date__range=(self.from_date, self.to_date),
                status=CalendarDate.CalendarStatus.BLOCKED,
            ).values_list("date", flat=True)
        )

        if existing_blocked_dates:
            existing_blocked_dates_str = ", ".join(
                existing_blocked_date.isoformat()
                for existing_blocked_date in existing_blocked_dates
            )
            raise ValidationError(
                {
                    "detail": _(
                        "Some dates are already blocked: {existing_blocked_dates}"
                    ).format(existing_blocked_dates=existing_blocked_dates_str)
                }
            )

        days = self._build_days()

        calendar_dates = [
            CalendarDate(
                property=self.property,
                date=day,
                status=CalendarDate.CalendarStatus.BLOCKED,
            )
            for day in days
            if day not in existing_blocked_dates
        ]

        CalendarDate.objects.bulk_create(
            calendar_dates,
            ignore_conflicts=True,  # protects from unique(property, date)
        )

        return days

    @transaction.atomic
    def unblock(self):
        self._validate_booked_dates()

        calendar_dates = CalendarDate.objects.filter(
            property=self.property,
            date__range=(self.from_date, self.to_date),
            status=CalendarDate.CalendarStatus.BLOCKED,
        )

        if not calendar_dates.exists():
            raise ValidationError(
                _("No blocked dates were found in the specified range")
            )

        days = list(calendar_dates.values_list("date", flat=True))
        calendar_dates.delete()

        return days

    @transaction.atomic
    def hold(self):
        self._validate_booked_dates()

        existing_blocked_dates = set(
            CalendarDate.objects.filter(
                property=self.property,
                date__range=(self.from_date, self.to_date),
                status=CalendarDate.CalendarStatus.BLOCKED,
            ).values_list("date", flat=True)
        )

        if existing_blocked_dates:
            existing_blocked_dates_str = ", ".join(
                existing_blocked_date.isoformat()
                for existing_blocked_date in existing_blocked_dates
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

    @transaction.atomic
    def unhold(self):
        self._validate_booked_dates()

        days = self._build_days()

        removed = []
        for day in days:
            cache_key = self._cache_key(day)
            if cache.get(cache_key):
                cache.delete(cache_key)
                removed.append(day)

        if not removed:
            raise ValidationError(_("No held dates were found in the specified range"))

        return removed


class BookingPriceService:
    def __init__(self):
        raw_service_fee = getattr(settings, "SERVICE_FEE", "20")
        try:
            self.server_fee = Decimal(str(raw_service_fee or "20"))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning(
                "Invalid SERVICE_FEE=%r. Falling back to 20.", raw_service_fee
            )
            self.server_fee = Decimal("20")

    def _date_range(self, start: date, end: date):
        current_date = start
        while current_date < end:
            yield current_date
            current_date += timedelta(days=1)

    def _get_property_price_for_day(
        self,
        day: date,
        property: Property,
    ):
        property_price = PropertyPrice.objects.filter(
            property=property,
            month_from__lte=day,
            month_to__gte=day,
        ).order_by("month_from", "created_at").first()
        if property_price:
            return property_price

        fallback_price = PropertyPrice.objects.filter(
            property=property,
        ).order_by("month_from", "created_at").first()
        if fallback_price:
            return fallback_price

        raise ValidationError(
            _("Pricing is not configured for this property")
        )

    def calculate(
        self,
        adults: int,
        children: int,
        check_in: date,
        check_out: date,
        property: Property,
    ):
        guests = adults + children

        included_guests = property.property_room.guests
        extra_persons = max(guests - included_guests, 0)

        base_total_price = Decimal("0")
        extra_total_price = Decimal("0")

        for day in self._date_range(check_in, check_out):
            property_price = self._get_property_price_for_day(day, property)
            base_day = (
                property_price.price_on_weekends
                if day.weekday() >= 4
                else property_price.price_on_working_days
            )
            base_total_price += base_day
            extra_total_price += property_price.price_per_person * extra_persons

        raw_subtotal = base_total_price + extra_total_price

        if property.currency == "USD":
            subtotal = to_uzs(raw_subtotal)
            logger.info("Subtotal %s", subtotal)
        elif property.currency == "UZS":
            subtotal = raw_subtotal
        else:
            raise ValidationError(_("Unsupported currency"))

        service_fee = subtotal * self.server_fee / Decimal("100")

        hold_amount = service_fee
        logger.info("Hold amount %s", hold_amount)
        charge_amount = service_fee * Decimal("0.50")
        logger.info("Charge amount %s", charge_amount)

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
    def __init__(self, client: Client, property: Property):
        self.client = client
        self.property = property
        self.booking_price_service = BookingPriceService()
        self.plum_service = PlumAPIService()

    def _partner_booking_data(self, booking: Booking, event: str) -> dict:
        client_name = f"{booking.client.first_name} {booking.client.last_name}".strip()
        return {
            "type": "booking_event",
            "event": event,
            "booking_id": str(booking.guid),
            "booking_number": booking.booking_number,
            "status": booking.status,
            "property_title": booking.property.title,
            "check_in": str(booking.check_in),
            "check_out": str(booking.check_out),
            "client_name": client_name,
        }

    def _notify_partner_booking_event(
        self,
        booking: Booking,
        title: str,
        message: str,
        event: str,
        notify_partner: bool = True,
    ):
        if not notify_partner:
            return
        NotificationService.send_to_partner(
            partner=booking.property.partner,
            title=title,
            message=message,
            data=self._partner_booking_data(booking, event),
        )

    def _date_range(self, check_in: date, check_out: date):
        current_date = check_in
        while current_date < check_out:
            yield current_date
            current_date += timedelta(days=1)

    def _hold_cache_key(self, day: date):
        return f"calendar:hold:{self.property.guid}:{day.isoformat()}"

    @transaction.atomic
    def create_booking(self, check_in: date, check_out: date, data):
        Property.objects.select_for_update().get(pk=self.property.pk)

        date_from = check_in
        date_to = check_out - timedelta(days=1)

        unavailable_days = CalendarDate.objects.filter(
            property=self.property,
            date__range=(date_from, date_to),
            status__in=[
                CalendarDate.CalendarStatus.BOOKED,
                CalendarDate.CalendarStatus.BLOCKED,
            ],
        )

        if unavailable_days.exists():
            unavailable_days_str = ", ".join(
                day.isoformat()
                for day in unavailable_days.values_list("date", flat=True)
            )
            raise ValidationError(
                _(
                    "This property is temporarily reserved for these dates. "
                    "The first guest has 30 minutes to complete payment. "
                    "If they do not pay in time, the property will become available again. "
                    "Unavailable dates: {unavailable_days}"
                ).format(unavailable_days=unavailable_days_str)
            )

        held_days = []
        days = list(self._date_range(check_in, check_out))
        for day in days:
            if cache.get(self._hold_cache_key(day)):
                held_days.append(day)

        if held_days:
            held_days_str = ", ".join(
                day.isoformat() for day in self._date_range(check_in, check_out)
            )
            raise ValidationError(
                _(
                    "This property is temporarily reserved for these dates. "
                    "The first guest has 30 minutes to complete payment. "
                    "If they do not pay in time, the property will become available again. "
                    "Reserved dates: {held_days}"
                ).format(held_days=held_days_str)
            )

        if getattr(self.property, "weekend_only_sunday_inclusive", False):
            if check_in.weekday() not in (4, 5):
                raise ValidationError(
                    _(
                        "This property can only be booked with check-in on Friday or Saturday."
                    )
                )
            day = check_in
            has_sunday = False
            while day < check_out:
                if day.weekday() == 6:
                    has_sunday = True
                    break
                day += timedelta(days=1)
            if not has_sunday:
                raise ValidationError(
                    _(
                        "This property requires the stay to include Sunday. "
                        "Please choose check-out on Monday or later."
                    )
                )

        booking_price = self.booking_price_service.calculate(
            adults=data["adults"],
            children=data["children"],
            check_in=check_in,
            check_out=check_out,
            property=self.property,
        )

        try:
            hold = self.plum_service.create_hold(
                client=self.client,
                card_id=data["card_id"],
                amount=booking_price["hold_amount"],
            )

            plum_transaction = PlumTransaction.objects.create(
                transaction_id=hold["result"]["transactionId"],
                hold_id=hold["result"]["holdId"],
                amount=hold["result"]["totalAmount"],
                card_id=hold["result"]["cardId"],
                extra_id=hold["result"]["extraId"],
                status=PlumTransactionStatus.PENDING,
            )
        except PlumAPIError as plum_api_error:
            if plum_api_error.status_code == 403:
                raise PermissionDenied(plum_api_error.message)
            raise ValidationError(plum_api_error.message)

        booking = Booking.objects.create(
            property=self.property,
            client=self.client,
            check_in=check_in,
            check_out=check_out,
            adults=data["adults"],
            children=data["children"],
            babies=data["babies"],
            status=Booking.BookingStatus.PENDING,
        )

        BookingPrice.objects.create(
            booking=booking,
            subtotal=booking_price["subtotal"],
            hold_amount=booking_price["hold_amount"],
            charge_amount=booking_price["charge_amount"],
            service_fee=booking_price["service_fee"],
            service_fee_percentage=booking_price["service_fee_percentage"],
        )
        BookingTransaction.objects.create(
            booking=booking,
            plum_transaction=plum_transaction,
        )

        CalendarDate.objects.bulk_create(
            [
                CalendarDate(
                    property=self.property,
                    date=day,
                    status=CalendarDate.CalendarStatus.BOOKED,
                )
                for day in days
            ],
            ignore_conflicts=True,
        )

        NotificationService.send_to_client(
            client=booking.client,
            title="Booking created",
            message=(
                f"We have placed a hold of {booking_price['hold_amount']} UZS for your booking. "
                f"You have 30 minutes to complete payment; otherwise the property will be released "
                f"and available for others. The host will confirm your booking soon."
            ),
            notification_type=Notification.NotificationType.SYSTEM,
            data={"booking_id": str(booking.guid)},
        )
        NotificationService.send_to_partner(
            partner=booking.property.partner,
            title="New booking request",
            message=(
                f"You have a new booking request for {booking.property.title}. "
                f"Please review and confirm it in time."
            ),
            data=self._partner_booking_data(booking, "booking_requested"),
        )

        from .tasks import auto_cancel_booking

        auto_cancel_booking.apply_async(
            kwargs={"booking_id": booking.guid},
            countdown=60 * 30,
        )

        send_partner_telegram_msg.delay(
            partner_id=booking.property.partner.id,
            message=f"Yangi bron! {booking.property.title} -- {booking.check_in} - {booking.check_out}\nSumma:{booking.booking_price.subtotal * Decimal('0.9')}\nMijozni yo‘qotmaslik uchun bronni hozir tasdiqlang",
        )

        return booking, hold

    @transaction.atomic
    def cancel_booking(self, booking: Booking, notify_partner: bool = True):
        if not client_can_cancel(booking):
            raise ValidationError(get_cancellation_error_message(booking))

        if booking.status == Booking.BookingStatus.PENDING:
            booking_transactions = booking.transactions.select_related(
                "plum_transaction"
            ).first()
            if booking_transactions:
                plum_transaction = booking_transactions.plum_transaction
                try:
                    self.plum_service.dismiss_hold(
                        transaction_id=plum_transaction.transaction_id,
                        hold_id=plum_transaction.hold_id,
                    )
                except PlumAPIError as plum_api_error:
                    if plum_api_error.status_code == 403:
                        raise PermissionDenied(plum_api_error.message)
                    raise ValidationError(plum_api_error.message)
                plum_transaction.status = PlumTransactionStatus.DISMISSED
                plum_transaction.save(update_fields=["status"])

        CalendarDate.objects.filter(
            property=booking.property,
            date__range=(booking.check_in, booking.check_out - timedelta(days=1)),
        ).delete()

        booking.status = Booking.BookingStatus.CANCELLED
        booking.cancellation_reason = Booking.BookingCancellationReason.USER_CANCELLED
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=["status", "cancellation_reason", "cancelled_at"])
        self._notify_partner_booking_event(
            booking=booking,
            title="Booking cancelled by client",
            message=f"The client cancelled booking {booking.booking_number} for {booking.property.title}.",
            event="booking_cancelled_by_client",
            notify_partner=notify_partner,
        )
        return booking

    @transaction.atomic
    def partner_accept(self, booking: Booking, notify_partner: bool = True):
        if booking.status != Booking.BookingStatus.PENDING:
            raise ValidationError(
                _("You can only accept bookings with **pending** statuses")
            )

        booking.status = Booking.BookingStatus.CONFIRMED
        booking.confirmed_at = timezone.now()
        booking.save(update_fields=["status", "confirmed_at"])

        NotificationService.send_to_client(
            client=booking.client,
            title="Подтверждение бронирования✅",
            message=(
                f"Бронирование объекта <<{booking.property.title}>> успешно подтверждено.\n"
                f"Желаем вам приятного проживания😊"
            ),
            notification_type=Notification.NotificationType.BOOKING_CONFIRMED,
            data={"booking_id": str(booking.guid)},
        )
        self._notify_partner_booking_event(
            booking=booking,
            title="Booking accepted",
            message=f"You accepted booking {booking.booking_number} for {booking.property.title}.",
            event="booking_confirmed",
            notify_partner=notify_partner,
        )

        tz = timezone.get_current_timezone()
        check_in_time = timezone.make_aware(
            datetime.combine(
                booking.check_in, booking.property.property_detail.check_in
            ),
            tz,
        )
        arrival_deadline = check_in_time + timedelta(hours=3)

        from .tasks import auto_complete_booking

        auto_complete_booking.apply_async(
            kwargs={"booking_id": str(booking.guid)},
            eta=arrival_deadline,
        )

        return booking

    @transaction.atomic
    def partner_cancel(self, booking: Booking, notify_partner: bool = True):
        """
        Partner cancellation rule:
        - Only PENDING bookings can be canceled by partner
        - CONFIRMED bookings can't be canceled
        """
        if booking.status not in {
            Booking.BookingStatus.PENDING,
        }:
            raise ValidationError(
                _("Partner can cancel only bookings with status `PENDING`")
            )

        if booking.status == Booking.BookingStatus.PENDING:
            booking_transactions = booking.transactions.select_related(
                "plum_transaction"
            ).first()
            if booking_transactions:
                plum_transaction = booking_transactions.plum_transaction
                try:
                    self.plum_service.dismiss_hold(
                        transaction_id=plum_transaction.transaction_id,
                        hold_id=plum_transaction.hold_id,
                    )
                except PlumAPIError as plum_api_error:
                    if plum_api_error.status_code == 403:
                        raise PermissionDenied(plum_api_error.message)
                    raise ValidationError(plum_api_error.message)
                plum_transaction.status = PlumTransactionStatus.DISMISSED
                plum_transaction.save(update_fields=["status"])

        CalendarDate.objects.filter(
            property=booking.property,
            date__range=(booking.check_in, booking.check_out - timedelta(days=1)),
        ).delete()

        booking.status = Booking.BookingStatus.CANCELLED
        booking.cancellation_reason = (
            Booking.BookingCancellationReason.PARTNER_CANCELLED
        )
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=["status", "cancellation_reason", "cancelled_at"])

        NotificationService.send_to_client(
            client=booking.client,
            title="Бронирование отменено❌",
            message=(
                f"К сожалению, владелец объекта <<{booking.property.title}>> отменил ваше бронирование\n"
                f"Пожалуйста, выберите другой вариант — мы всегда рядом, чтобы помочь😊"
            ),
            notification_type=Notification.NotificationType.BOOKING_CANCELLED,
            data={"booking_id": str(booking.guid)},
        )
        self._notify_partner_booking_event(
            booking=booking,
            title="Booking cancelled",
            message=f"You cancelled booking {booking.booking_number} for {booking.property.title}.",
            event="booking_cancelled_by_partner",
            notify_partner=notify_partner,
        )

        return booking

    @transaction.atomic
    def system_cancel_booking(self, booking: Booking):
        if booking.status != Booking.BookingStatus.PENDING:
            return  # idempotency(operation)

        booking_transaction = booking.transactions.select_related(
            "plum_transaction"
        ).first()

        if booking_transaction:
            plum_transaction = booking_transaction.plum_transaction
            self.plum_service.dismiss_hold(
                transaction_id=plum_transaction.transaction_id,
                hold_id=plum_transaction.hold_id,
            )
            plum_transaction.status = PlumTransactionStatus.DISMISSED
            plum_transaction.save(update_fields=["status"])

        CalendarDate.objects.filter(
            property=booking.property,
            date__range=(booking.check_in, booking.check_out - timedelta(days=1)),
        ).delete()

        booking.status = Booking.BookingStatus.CANCELLED
        booking.cancellation_reason = (
            Booking.BookingCancellationReason.SYSTEM_TIMEOUT,
        )
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=["status", "cancellation_reason", "cancelled_at"])

        NotificationService.send_to_client(
            client=booking.client,
            title="Booking cancelled",
            message=(
                f"Your booking for {booking.property.title} was not confirmed in time "
                f"and has been released. The property is available again for these dates. "
                f"Please choose another option if you still wish to book."
            ),
            notification_type=Notification.NotificationType.SYSTEM,
            data={"booking_id": str(booking.guid)},
        )
        self._notify_partner_booking_event(
            booking=booking,
            title="Booking auto-cancelled",
            message=(
                f"Booking {booking.booking_number} for {booking.property.title} "
                f"was auto-cancelled because it was not confirmed in time."
            ),
            event="booking_auto_cancelled",
            notify_partner=True,
        )

    @transaction.atomic
    def system_complete_booking(self, booking: Booking):
        if booking.status != Booking.BookingStatus.CONFIRMED:
            return

        booking_transaction = booking.transactions.select_related(
            "plum_transaction"
        ).first()

        if not booking_transaction:
            logger.warning(
                "system_complete_booking: no transaction for booking",
                extra={"booking_id": str(booking.guid)},
            )
            return

        plum_transaction = booking_transaction.plum_transaction
        try:
            charge_transaction = self.plum_service.charge_hold(
                transaction_id=plum_transaction.transaction_id,
                hold_id=plum_transaction.hold_id,
                charge_amount=booking.booking_price.charge_amount,
            )
        except PlumAPIError as plum_api_error:
            logger.warning(
                "system_complete_booking: charge_hold PlumAPIError (proceeding): %s",
                plum_api_error.message,
                extra={"booking_id": str(booking.guid), "status_code": plum_api_error.status_code},
            )
            charge_transaction = None
        except Exception as exc:
            logger.exception(
                "system_complete_booking: charge_hold failed unexpectedly: %s",
                exc,
                extra={"booking_id": str(booking.guid)},
            )
            charge_transaction = None

        booking.status = Booking.BookingStatus.COMPLETED
        booking.completed_at = timezone.now()
        booking.save(update_fields=["status", "completed_at"])

        if charge_transaction:
            try:
                BookingTransaction.objects.create(
                    booking=booking,
                    plum_transaction=charge_transaction,
                )
            except Exception as e:
                logger.exception(
                    "system_complete_booking: BookingTransaction create failed: %s",
                    e,
                    extra={"booking_id": str(booking.guid)},
                )

        NotificationService.send_to_client(
            client=booking.client,
            title="Бронирование завершено🎉",
            message=(
                f"Бронирование объекта «{booking.property.title}» успешно завершено🏠\n"
                f"Ранее захолдированная сумма: {booking.booking_price.hold_amount} сум\n"
                f"Сумма к оплате по бронированию: {booking.booking_price.charge_amount} сум\n"
                f"Спасибо, что выбрали нас😊"
            ),
            notification_type=Notification.NotificationType.SYSTEM,
            data={"booking_id": str(booking.guid)},
        )
        self._notify_partner_booking_event(
            booking=booking,
            title="Booking completed",
            message=f"Booking {booking.booking_number} for {booking.property.title} was completed.",
            event="booking_auto_completed",
            notify_partner=True,
        )

    @transaction.atomic
    def complete_booking(self, booking: Booking, notify_partner: bool = True):
        if booking.status != Booking.BookingStatus.CONFIRMED:
            raise ValidationError(_("Only confirmed bookings can be completed"))

        booking_transaction = booking.transactions.select_related(
            "plum_transaction"
        ).first()

        if not booking_transaction:
            raise ValidationError(_("Payment transaction not found"))

        plum_transaction = booking_transaction.plum_transaction
        try:
            charge_amount = booking.booking_price.charge_amount
            charge_transaction = self.plum_service.charge_hold(
                transaction_id=plum_transaction.transaction_id,
                hold_id=plum_transaction.hold_id,
                charge_amount=charge_amount,
            )
        except PlumAPIError as plum_api_error:
            if plum_api_error.status_code == 403:
                raise PermissionDenied(plum_api_error.message)
            logger.warning(
                "complete_booking: charge_hold PlumAPIError (proceeding with completion): %s",
                plum_api_error.message,
                extra={"booking_id": str(booking.guid), "status_code": plum_api_error.status_code},
            )
            charge_transaction = None
        except Exception as exc:
            logger.exception(
                "complete_booking: charge_hold failed unexpectedly: %s",
                exc,
                extra={"booking_id": str(booking.guid)},
            )
            charge_transaction = None

        # Pul yechilganidan keyin darhol statusni yangilaymiz — keyingi qadamlarda
        # xato bo‘lsa ham bron completed bo‘lib qoladi
        booking.status = Booking.BookingStatus.COMPLETED
        booking.completed_at = timezone.now()
        booking.save(update_fields=["status", "completed_at"])

        if charge_transaction:
            try:
                BookingTransaction.objects.create(
                    booking=booking, plum_transaction=charge_transaction
                )
            except Exception as e:
                logger.exception(
                    "BookingTransaction create failed after successful charge: %s",
                    e,
                    extra={"booking_id": str(booking.guid)},
                )

        try:
            NotificationService.send_to_client(
                client=booking.client,
                title="Бронирование завершено🎉",
                message=(
                    f"Бронирование объекта «{booking.property.title}» успешно завершено🏠\n"
                    f"Ранее захолдированная сумма: {booking.booking_price.hold_amount} сум\n"
                    f"Сумма к оплате по бронированию: {booking.booking_price.charge_amount} сум\n"
                    f"Спасибо, что выбрали нас😊"
                ),
            )
        except Exception as e:
            logger.exception("Notification failed after booking complete: %s", e)

        self._notify_partner_booking_event(
            booking=booking,
            title="Booking completed",
            message=f"You completed booking {booking.booking_number} for {booking.property.title}.",
            event="booking_completed",
            notify_partner=notify_partner,
        )

        return booking

    @transaction.atomic
    def mark_no_show(self, booking: Booking, notify_partner: bool = True):
        """
        Partner cancellation rule:
        - If client hasn't arrived at the property
        - We will charge him the amount withheld
        """
        if booking.status != Booking.BookingStatus.CONFIRMED:
            raise ValidationError(_("Only confirmed bookings can be marked as no-show"))

        booking_transaction = booking.transactions.select_related(
            "plum_transaction"
        ).first()

        if booking_transaction:
            plum_transaction = booking_transaction.plum_transaction
            try:
                charge_transaction = self.plum_service.charge_hold(
                    transaction_id=plum_transaction.transaction_id,
                    hold_id=plum_transaction.hold_id,
                    charge_amount=booking.booking_price.hold_amount,
                )
            except PlumAPIError as plum_api_error:
                raise ValidationError(plum_api_error.message)

            if charge_transaction:
                try:
                    BookingTransaction.objects.create(
                        booking=booking,
                        plum_transaction=charge_transaction,
                    )
                except Exception as e:
                    logger.exception(
                        "mark_no_show: BookingTransaction create failed: %s",
                        e,
                        extra={"booking_id": str(booking.guid)},
                    )

        booking.status = Booking.BookingStatus.CANCELLED
        booking.cancellation_reason = Booking.BookingCancellationReason.USER_NO_SHOW
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=["status", "cancellation_reason", "cancelled_at"])
        self._notify_partner_booking_event(
            booking=booking,
            title="No-show recorded",
            message=f"No-show recorded for booking {booking.booking_number} at {booking.property.title}.",
            event="booking_no_show",
            notify_partner=notify_partner,
        )
        return booking
