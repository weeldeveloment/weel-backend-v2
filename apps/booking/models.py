import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from property.models import Property
from users.models.clients import Client
from payment.models import PlumTransaction
from payment.choices import Currency
from shared.models import HardDeleteBaseModel


# Create your models here.


class Booking(HardDeleteBaseModel):
    class BookingStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")
        COMPLETED = "completed", _("Completed")

    class BookingCancellationReason(models.TextChoices):
        USER_CANCELLED = "user_cancelled", _("User cancelled")
        USER_NO_SHOW = "user_no_show", _("User did not arrive")
        PARTNER_CANCELLED = "partner_cancelled", _("Partner cancelled")
        SYSTEM_TIMEOUT = "system_timeout", _("System timeout")

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="booking_client",
        verbose_name=_("Client"),
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="booking_property",
        verbose_name=_("Property"),
    )
    check_in = models.DateField(verbose_name=_("Check in"))
    check_out = models.DateField(verbose_name=_("Check out"))
    booking_number = models.CharField(
        max_length=7,
        unique=True,
        db_index=True,
        verbose_name=_("Booking number"),
    )
    adults = models.PositiveSmallIntegerField(
        default=1,
        db_default="1",
        verbose_name=_("Adults"),
    )
    children = models.PositiveSmallIntegerField(
        default=0,
        db_default="0",
        verbose_name=_("Children"),
    )
    babies = models.PositiveSmallIntegerField(
        default=0,
        db_default="0",
        verbose_name=_("Babies"),
    )
    reminder_sent = models.BooleanField(default=False, verbose_name=_("Reminder sent"))
    # Tracks last payment-deadline reminder sent for PENDING bookings (24h, 6h, 1h left)
    payment_reminder_stage = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name=_("Payment reminder stage"),
    )
    status = models.CharField(
        max_length=20,
        db_index=True,
        choices=BookingStatus,
        default=BookingStatus.PENDING,
        db_default=BookingStatus.PENDING,
        verbose_name=_("Status"),
    )
    cancellation_reason = models.CharField(
        max_length=100,
        db_index=True,
        choices=BookingCancellationReason,
        null=True,
        blank=True,
        verbose_name=_("Cancellation reason"),
    )
    confirmed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Confirmed at")
    )
    cancelled_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Cancelled at")
    )
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Completed at")
    )

    class Meta:
        verbose_name = _("Booking")
        verbose_name_plural = _("Bookings")
        constraints = [
            models.CheckConstraint(
                check=models.Q(check_out__gt=models.F("check_in")),
                name="check_out_after_check_in",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.booking_number:
            self.booking_number = "".join(str(secrets.randbelow(10)) for _ in range(7))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Booking: {self.guid} | {self.property} | {self.check_in} -> {self.check_out}"

    def __repr__(self):
        return f"Booking={self.guid} property={self.property} check_in={self.check_in} check_out={self.check_out}"


class BookingPrice(HardDeleteBaseModel):
    booking = models.OneToOneField(
        Booking,
        on_delete=models.CASCADE,
        related_name="booking_price",
        verbose_name=_("Booking"),
    )
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Subtotal")
    )
    hold_amount = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Hold amount")
    )
    charge_amount = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Charge amount")
    )
    service_fee = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Service fee")
    )
    service_fee_percentage = models.PositiveSmallIntegerField(
        default=20, verbose_name=_("Service fee percentage")
    )

    class Meta:
        verbose_name = _("Booking price")
        verbose_name_plural = _("Booking prices")

    def __str__(self):
        return f"Booking: {self.booking.guid} | Subtotal: {self.subtotal} | Hold amount: {self.hold_amount} | Charge amount: {self.charge_amount}"

    def __repr__(self):
        return (
            f"<BookingPrice id={self.guid}"
            f"booking_id={self.booking.guid} "
            f"subtotal={self.subtotal} "
            f"hold_amount={self.hold_amount} "
            f"charge_amount={self.charge_amount} "
            f"service_fee={self.service_fee}>"
        )


class BookingTransaction(HardDeleteBaseModel):
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=_("Booking"),
    )
    plum_transaction = models.ForeignKey(
        PlumTransaction,
        on_delete=models.PROTECT,
        related_name="booking_transactions",
        verbose_name=_("Plum transaction"),
    )

    class Meta:
        verbose_name = _("Booking transaction")
        verbose_name_plural = _("Booking transactions")

    def __str__(self):
        return f"Booking {self.booking.guid} | Transaction {self.plum_transaction}"

    def __repr__(self):
        return (
            f"<BookingTransaction id={self.guid} "
            f"booking_id={self.booking.guid} "
            f"plum_transaction_id={self.plum_transaction}>"
        )


class CalendarDate(HardDeleteBaseModel):
    class CalendarStatus(models.TextChoices):
        AVAILABLE = "available", _("Available")
        BOOKED = "booked", _("Booked")
        BLOCKED = "blocked", _("Blocked")
        HELD = "held", _("Held")

    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="calendar_date_property",
        verbose_name=_("Property"),
    )
    status = models.CharField(
        max_length=20,
        db_index=True,
        choices=CalendarStatus,
        default=CalendarStatus.AVAILABLE,
        db_default=CalendarStatus.AVAILABLE,
        verbose_name=_("Status"),
    )
    date = models.DateField(db_index=True, verbose_name=_("Date"))

    class Meta:
        verbose_name = _("Calendar Date")
        verbose_name_plural = _("Calendar Dates")
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "date"],
                name="unique_property_date",
            )
        ]

    def clean(self):
        calendar_date = CalendarDate.objects.filter(
            property=self.property,
            date=self.date,
        )
        if self.pk:
            calendar_date = calendar_date.exclude(pk=self.pk)

        if calendar_date.exists():
            raise ValidationError(
                {"date": _("Calendar date for this property already exists")}
            )
