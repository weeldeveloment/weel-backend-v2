import uuid
import secrets

from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MinValueValidator,
    MaxValueValidator,
)
from django.db import models
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from core import settings
from payment.choices import Currency
from payment.models import PlumTransaction
from users.models.clients import Client
from users.models.partners import Partner
from shared.compress_image import property_image_compress
from shared.models import BaseModel, HardDeleteBaseModel, VerifiedByMixin


class VerificationStatus(models.TextChoices):
    WAITING = ("waiting", _("Waiting"))
    ACCEPTED = ("accepted", _("Accepted"))
    CANCELLED = ("cancelled", _("Cancelled"))


# ──────────────────────────────────────────────
# Reference / Lookup tables
# ──────────────────────────────────────────────


class MedicalSpecialization(BaseModel):
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"))
    icon = models.FileField(
        upload_to=".sanatorium/specialization_icons/",
        verbose_name=_("Icon"),
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
    )

    class Meta:
        verbose_name = _("Medical specialization")
        verbose_name_plural = _("Medical specializations")

    def __str__(self):
        return self.title_en


class Treatment(BaseModel):
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"))
    icon = models.FileField(
        upload_to=".sanatorium/treatment_icons/",
        verbose_name=_("Icon"),
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
    )

    class Meta:
        verbose_name = _("Treatment")
        verbose_name_plural = _("Treatments")

    def __str__(self):
        return self.title_en


class RoomType(BaseModel):
    title_en = models.CharField(max_length=55, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=55, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=55, verbose_name=_("Title (uz)"))

    class Meta:
        verbose_name = _("Room type")
        verbose_name_plural = _("Room types")

    def __str__(self):
        return self.title_en


class PackageType(BaseModel):
    title_en = models.CharField(max_length=55, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=55, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=55, verbose_name=_("Title (uz)"))
    duration_days = models.PositiveSmallIntegerField(verbose_name=_("Duration (days)"))

    class Meta:
        verbose_name = _("Package type")
        verbose_name_plural = _("Package types")
        ordering = ["duration_days"]

    def __str__(self):
        return f"{self.title_en} ({self.duration_days} days)"


class RoomAmenity(BaseModel):
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"))
    icon = models.FileField(
        upload_to=".sanatorium/amenity_icons/",
        verbose_name=_("Icon"),
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
    )

    class Meta:
        verbose_name = _("Room amenity")
        verbose_name_plural = _("Room amenities")

    def __str__(self):
        return self.title_en


# ──────────────────────────────────────────────
# Sanatorium (main entity)
# ──────────────────────────────────────────────


class SanatoriumLocation(HardDeleteBaseModel):
    latitude = models.DecimalField(
        max_digits=17, decimal_places=14, verbose_name=_("Latitude")
    )
    longitude = models.DecimalField(
        max_digits=17, decimal_places=14, verbose_name=_("Longitude")
    )
    city = models.CharField(max_length=100, verbose_name=_("City"))
    country = models.CharField(max_length=100, verbose_name=_("Country"))

    class Meta:
        verbose_name = _("Sanatorium location")
        verbose_name_plural = _("Sanatorium locations")

    def __str__(self):
        return f"{self.city} | {self.country}"


class SanatoriumTreatment(models.Model):
    """Through model for Sanatorium–Treatment M2M (table: sanatorium_sanatorium_treatment)."""
    sanatorium = models.ForeignKey(
        "Sanatorium",
        on_delete=models.CASCADE,
        related_name="sanatorium_treatment_set",
    )
    treatment = models.ForeignKey(
        Treatment,
        on_delete=models.CASCADE,
        related_name="sanatorium_treatment_set",
    )
    is_paid_extra = models.BooleanField(
        default=False, db_default=False, verbose_name=_("Is paid extra")
    )

    class Meta:
        db_table = "sanatorium_sanatorium_treatment"
        unique_together = [[".sanatorium", "treatment"]]


class Sanatorium(HardDeleteBaseModel, VerifiedByMixin):
    verification_status = models.CharField(
        max_length=10,
        choices=VerificationStatus,
        default=VerificationStatus.WAITING,
        db_default=VerificationStatus.WAITING,
        verbose_name=_("Verification status"),
    )
    title = models.CharField(max_length=150, verbose_name=_("Title"))
    description_en = models.TextField(
        blank=True, default="", verbose_name=_("Description (en)")
    )
    description_ru = models.TextField(
        blank=True, default="", verbose_name=_("Description (ru)")
    )
    description_uz = models.TextField(
        blank=True, default="", verbose_name=_("Description (uz)")
    )
    location = models.OneToOneField(
        SanatoriumLocation,
        on_delete=models.CASCADE,
        related_name=".sanatorium",
        verbose_name=_("Location"),
    )
    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="sanatoriums",
        verbose_name=_("Partner"),
    )
    specializations = models.ManyToManyField(
        MedicalSpecialization,
        related_name="sanatoriums",
        blank=True,
        verbose_name=_("Medical specializations"),
    )
    treatments = models.ManyToManyField(
        Treatment,
        related_name="sanatoriums",
        blank=True,
        through=SanatoriumTreatment,
        verbose_name=_("Treatments"),
    )
    check_in_time = models.TimeField(
        default=time(14, 0), verbose_name=_("Check-in time")
    )
    check_out_time = models.TimeField(
        default=time(12, 0), verbose_name=_("Check-out time")
    )
    comment_count = models.PositiveIntegerField(
        default=0, db_default=0, verbose_name=_("Comment count")
    )
    is_archived = models.BooleanField(
        default=False, db_default=False, verbose_name=_("Is archived")
    )
    address = models.CharField(
        max_length=255, default="", blank=True, verbose_name=_("Address")
    )
    inn = models.CharField(
        max_length=50, default="", blank=True, verbose_name=_("INN")
    )
    legal_name = models.CharField(
        max_length=255, default="", blank=True, verbose_name=_("Legal name")
    )
    meals_per_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name=_("Meals per day")
    )
    region = models.ForeignKey(
        "property.Region",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sanatoriums",
        verbose_name=_("Region"),
    )
    total_rooms_count = models.PositiveIntegerField(
        default=0, db_default=0, verbose_name=_("Total rooms count")
    )

    class Meta:
        verbose_name = _("Sanatorium")
        verbose_name_plural = _("Sanatoriums")
        constraints = [
            models.UniqueConstraint(
                fields=["title"],
                condition=models.Q(is_archived=False),
                name="unique_active_sanatorium_title",
            ),
            models.UniqueConstraint(
                fields=["location"],
                condition=models.Q(is_archived=False),
                name="unique_active_sanatorium_location",
            ),
        ]

    def save(self, *args, **kwargs):
        self.is_verified = self.verification_status == VerificationStatus.ACCEPTED
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class SanatoriumImage(HardDeleteBaseModel):
    def _upload_directory_path(self, filename: str) -> str:
        extension = filename.split(".")[-1]
        unique_name = uuid.uuid4().hex
        return f".sanatorium/images/{unique_name}.{extension}"

    sanatorium = models.ForeignKey(
        Sanatorium,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name=_("Sanatorium"),
    )
    order = models.SmallIntegerField(default=1, db_default=1, verbose_name=_("Order"))
    image = models.ImageField(
        upload_to=_upload_directory_path,
        max_length=250,
        verbose_name=_("Image"),
    )
    is_pending = models.BooleanField(
        default=True, db_default=True, verbose_name=_("Is pending")
    )

    class Meta:
        verbose_name = _("Sanatorium image")
        verbose_name_plural = _("Sanatorium images")
        ordering = ["order"]

    def clean(self):
        if self.image:
            if self.image.size > settings.MAX_IMAGE_SIZE:
                raise ValidationError(
                    {"image": _("Image file too large, maximum size is 20MB")}
                )
            extension = self.image.name.split(".")[-1].lower()
            if extension not in settings.ALLOWED_PHOTO_EXTENSION:
                raise ValidationError(
                    {
                        "image": _(
                            "Invalid image format, allowed are: jpg, jpeg, png, heif, heic"
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if self.image and self.image.size > settings.PHOTO_SIZE_TO_COMPRESS:
            self.image = property_image_compress(self.image)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Image #{self.order} for {self.sanatorium.title}"


# ──────────────────────────────────────────────
# Sanatorium Rooms
# ──────────────────────────────────────────────


class SanatoriumRoom(HardDeleteBaseModel):
    sanatorium = models.ForeignKey(
        Sanatorium,
        on_delete=models.CASCADE,
        related_name="rooms",
        verbose_name=_("Sanatorium"),
    )
    room_type = models.ForeignKey(
        RoomType,
        on_delete=models.CASCADE,
        related_name="sanatorium_rooms",
        verbose_name=_("Room type"),
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=150, verbose_name=_("Title"))
    description_en = models.TextField(
        blank=True, default="", verbose_name=_("Description (en)")
    )
    description_ru = models.TextField(
        blank=True, default="", verbose_name=_("Description (ru)")
    )
    description_uz = models.TextField(
        blank=True, default="", verbose_name=_("Description (uz)")
    )
    area = models.DecimalField(
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name=_("Area (m²)"),
    )
    bed_type = models.CharField(
        max_length=100, blank=True, default="", verbose_name=_("Bed type")
    )
    bed_count = models.PositiveSmallIntegerField(
        default=1, db_default=1, verbose_name=_("Bed count")
    )
    capacity = models.PositiveSmallIntegerField(
        default=2, db_default=2, verbose_name=_("Capacity (guests)")
    )
    amenities = models.ManyToManyField(
        RoomAmenity,
        related_name="rooms",
        blank=True,
        verbose_name=_("Amenities"),
    )

    class Meta:
        verbose_name = _("Sanatorium room")
        verbose_name_plural = _("Sanatorium rooms")

    def __str__(self):
        return f"{self.title} — {self.sanatorium.title}"


class SanatoriumRoomImage(HardDeleteBaseModel):
    def _upload_directory_path(self, filename: str) -> str:
        extension = filename.split(".")[-1]
        unique_name = uuid.uuid4().hex
        return f".sanatorium/room_images/{unique_name}.{extension}"

    room = models.ForeignKey(
        SanatoriumRoom,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name=_("Room"),
    )
    order = models.SmallIntegerField(default=1, db_default=1, verbose_name=_("Order"))
    image = models.ImageField(
        upload_to=_upload_directory_path,
        max_length=250,
        verbose_name=_("Image"),
    )

    class Meta:
        verbose_name = _("Room image")
        verbose_name_plural = _("Room images")
        ordering = ["order"]

    def save(self, *args, **kwargs):
        if self.image and self.image.size > settings.PHOTO_SIZE_TO_COMPRESS:
            self.image = property_image_compress(self.image)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Image #{self.order} for room {self.room.title}"


class SanatoriumRoomPrice(HardDeleteBaseModel):
    room = models.ForeignKey(
        SanatoriumRoom,
        on_delete=models.CASCADE,
        related_name="prices",
        verbose_name=_("Room"),
    )
    package_type = models.ForeignKey(
        PackageType,
        on_delete=models.CASCADE,
        related_name="room_prices",
        verbose_name=_("Package type"),
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Price")
    )
    currency = models.CharField(
        max_length=3,
        choices=Currency,
        default=Currency.UZS,
        db_default=Currency.UZS,
        verbose_name=_("Currency"),
    )

    class Meta:
        verbose_name = _("Room price")
        verbose_name_plural = _("Room prices")
        constraints = [
            models.UniqueConstraint(
                fields=["room", "package_type"],
                name="unique_room_package_price",
            ),
            models.CheckConstraint(
                check=models.Q(price__gte=0),
                name="sanatorium_room_price_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.room.title} / {self.package_type} — {self.price}"


# ──────────────────────────────────────────────
# Room Calendar (availability per room)
# ──────────────────────────────────────────────


class RoomCalendarDate(HardDeleteBaseModel):
    class CalendarStatus(models.TextChoices):
        AVAILABLE = "available", _("Available")
        BOOKED = "booked", _("Booked")
        BLOCKED = "blocked", _("Blocked")

    room = models.ForeignKey(
        SanatoriumRoom,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="calendar_dates",
        verbose_name=_("Room"),
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
        verbose_name = _("Room calendar date")
        verbose_name_plural = _("Room calendar dates")
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "date"],
                name="unique_room_calendar_date",
            )
        ]

    def __str__(self):
        return f"{self.room.title} — {self.date} ({self.status})"


# ──────────────────────────────────────────────
# Reviews
# ──────────────────────────────────────────────


class SanatoriumReview(HardDeleteBaseModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="sanatorium_reviews",
        verbose_name=_("Client"),
    )
    sanatorium = models.ForeignKey(
        Sanatorium,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("Sanatorium"),
    )
    rating = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        default=1.0,
        db_default=1.0,
        null=True,
        blank=True,
        verbose_name=_("Rating"),
        validators=[
            MinValueValidator(Decimal("1.0")),
            MaxValueValidator(Decimal("5.0")),
        ],
    )
    comment = models.TextField(blank=True, null=True, verbose_name=_("Comment"))
    is_hidden = models.BooleanField(
        null=True,
        blank=True,
        default=False,
        db_default=False,
        verbose_name=_("Hide"),
    )

    class Meta:
        verbose_name = _("Sanatorium review")
        verbose_name_plural = _("Sanatorium reviews")

    def __str__(self):
        return f"Review by {self.client} for {self.sanatorium}"


# ──────────────────────────────────────────────
# Favorites
# ──────────────────────────────────────────────


class SanatoriumFavorite(HardDeleteBaseModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="sanatorium_favorites",
        verbose_name=_("Client"),
    )
    sanatorium = models.ForeignKey(
        Sanatorium,
        on_delete=models.CASCADE,
        related_name="favorites",
        verbose_name=_("Sanatorium"),
    )

    class Meta:
        verbose_name = _("Sanatorium favorite")
        verbose_name_plural = _("Sanatorium favorites")
        constraints = [
            models.UniqueConstraint(
                fields=["client", ".sanatorium"],
                name="unique_client_sanatorium_favorite",
            )
        ]

    def __str__(self):
        return f"{self.client} ♥ {self.sanatorium}"


# ──────────────────────────────────────────────
# Booking
# ──────────────────────────────────────────────


class SanatoriumBooking(HardDeleteBaseModel):
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
        related_name="sanatorium_bookings",
        verbose_name=_("Client"),
    )
    sanatorium = models.ForeignKey(
        Sanatorium,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Sanatorium"),
    )
    room = models.ForeignKey(
        SanatoriumRoom,
        on_delete=models.CASCADE,
        related_name="bookings",
        verbose_name=_("Room"),
    )
    treatment = models.ForeignKey(
        Treatment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name=_("Treatment"),
    )
    specialization = models.ForeignKey(
        MedicalSpecialization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        verbose_name=_("Specialization"),
    )
    package_type = models.ForeignKey(
        PackageType,
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name=_("Package type"),
    )
    check_in = models.DateField(verbose_name=_("Check in"))
    check_out = models.DateField(verbose_name=_("Check out"))
    booking_number = models.CharField(
        max_length=7,
        unique=True,
        db_index=True,
        verbose_name=_("Booking number"),
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
    reminder_sent = models.BooleanField(
        default=False, verbose_name=_("Reminder sent")
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
        verbose_name = _("Sanatorium booking")
        verbose_name_plural = _("Sanatorium bookings")
        constraints = [
            models.CheckConstraint(
                check=models.Q(check_out__gt=models.F("check_in")),
                name="sanatorium_check_out_after_check_in",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.booking_number:
            self.booking_number = "".join(
                str(secrets.randbelow(10)) for _ in range(7)
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Booking #{self.booking_number} | {self.sanatorium} | {self.check_in} → {self.check_out}"


class SanatoriumBookingPrice(HardDeleteBaseModel):
    booking = models.OneToOneField(
        SanatoriumBooking,
        on_delete=models.CASCADE,
        related_name="booking_price",
        verbose_name=_("Booking"),
    )
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Subtotal")
    )
    hold_amount = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Hold amount (20%)")
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
        verbose_name = _("Sanatorium booking price")
        verbose_name_plural = _("Sanatorium booking prices")

    def __str__(self):
        return f"Booking #{self.booking.booking_number} | Subtotal: {self.subtotal}"


class SanatoriumBookingTransaction(HardDeleteBaseModel):
    booking = models.ForeignKey(
        SanatoriumBooking,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=_("Booking"),
    )
    plum_transaction = models.ForeignKey(
        PlumTransaction,
        on_delete=models.PROTECT,
        related_name="sanatorium_booking_transactions",
        verbose_name=_("Plum transaction"),
    )

    class Meta:
        verbose_name = _("Sanatorium booking transaction")
        verbose_name_plural = _("Sanatorium booking transactions")

    def __str__(self):
        return f"Booking #{self.booking.booking_number} | Txn {self.plum_transaction}"


# ──────────────────────────────────────────────
# Signals
# ──────────────────────────────────────────────


@receiver(post_save, sender=Sanatorium)
def update_pending_sanatorium_images(sender, instance: Sanatorium, created=False, **kwargs):
    previously_verified = getattr(instance, "_previous_is_verified", None)
    became_verified = bool(instance.is_verified) and not bool(previously_verified)

    if became_verified:
        SanatoriumImage.objects.filter(
            sanatorium=instance,
            is_pending=True,
        ).update(is_pending=False)


@receiver(pre_save, sender=Sanatorium)
def cache_sanatorium_verification_state(sender, instance: Sanatorium, **kwargs):
    if not instance.pk:
        instance._previous_is_verified = None
        return

    instance._previous_is_verified = (
        sender.objects.filter(pk=instance.pk)
        .values_list("is_verified", flat=True)
        .first()
    )


@receiver(post_save, sender=SanatoriumReview)
def update_sanatorium_comment_count_on_create(instance: SanatoriumReview, created=False, **kwargs):
    if created:
        Sanatorium.objects.filter(guid=instance.sanatorium.guid).update(
            comment_count=models.F("comment_count") + 1
        )


@receiver(post_delete, sender=SanatoriumReview)
def update_sanatorium_comment_count_on_delete(instance, **kwargs):
    Sanatorium.objects.filter(guid=instance.sanatorium.guid).update(
        comment_count=models.F("comment_count") - 1
    )
