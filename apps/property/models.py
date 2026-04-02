import uuid

from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django.db.models.signals import pre_save, post_save, post_delete
from django.utils import timezone
from django.core.validators import (
    FileExtensionValidator,
    MinValueValidator,
    MaxValueValidator,
)

from core import settings
from payment.choices import Currency
from users.models.clients import Client
from users.models.partners import Partner
from shared.compress_image import property_image_compress
from shared.models import BaseModel, HardDeleteBaseModel, VerifiedByMixin
from shared.date import month_start, month_end
from .manager import PropertyManager

class PropertyType(BaseModel):
    title_en = models.CharField(max_length=55, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=55, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=55, verbose_name=_("Title (uz)"))
    icon = models.FileField(
        upload_to="property/icons/",
        verbose_name=_("Icon"),
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
    )

    class Meta:
        verbose_name = _("Property type")
        verbose_name_plural = _("Property types")

    def __str__(self):
        return self.title_en

    def __repr__(self):
        return f"<PropertyType id={self.guid} title_en={self.title_en}>"


class PropertyService(BaseModel):
    title_en = models.CharField(max_length=55, verbose_name=_("Title (en)"))
    title_ru = models.CharField(max_length=55, verbose_name=_("Title (ru)"))
    title_uz = models.CharField(max_length=55, verbose_name=_("Title (uz)"))
    property_type = models.ForeignKey(
        PropertyType,
        on_delete=models.CASCADE,
        related_name="property_services",
        verbose_name=_("Property type"),
    )
    icon = models.FileField(
        verbose_name=_("Icon"),
        upload_to="property/icons/",
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
    )

    class Meta:
        verbose_name = _("Property service")
        verbose_name_plural = _("Property services")

    def __str__(self):
        return self.title_en

    def __repr__(self):
        return f"<PropertyService id={self.guid} title_en='{self.title_en}' property_type={self.property_type_id}>"


class Category(BaseModel):
    """Свежие предложения tablari uchun kategoriya — admin da yaratiladi, Property ga ulanadi."""

    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"), default="")
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"), default="")
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"), default="")
    icon = models.FileField(
        upload_to="property/icons/",
        verbose_name=_("Icon"),
        validators=[FileExtensionValidator(allowed_extensions=["svg"])],
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        ordering = ["title_uz"]

    def __str__(self):
        return self.title_uz

    def __repr__(self):
        return f"<Category id={self.guid} title_uz='{self.title_uz}'>"


class PropertyLocation(HardDeleteBaseModel):
    latitude = models.DecimalField(
        max_digits=17, decimal_places=14, verbose_name=_("Latitude")
    )
    longitude = models.DecimalField(
        max_digits=17, decimal_places=14, verbose_name=_("Longitude")
    )
    city = models.CharField(max_length=100, verbose_name=_("City"))
    country = models.CharField(max_length=100, verbose_name=_("Country"))

    class Meta:
        verbose_name = _("Property location")
        verbose_name_plural = _("Property locations")

    def __str__(self):
        return f"{self.city} | {self.country}"

    def __repr__(self):
        return f"<PropertyLocation id={self.guid} city='{self.city}' country='{self.country}' lat={self.latitude} lon={self.longitude}>"


class Region(BaseModel):
    """Oʻzbekiston viloyatlari (Toshkent shahri, viloyatlar, Qoraqalpogʻiston)."""

    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"))
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"))
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"))
    img = models.ImageField(
        upload_to="property/regions/",
        verbose_name=_("Image"),
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("Region")
        verbose_name_plural = _("Regions")
        ordering = ["title_uz"]

    def __str__(self):
        return self.title_uz

    def __repr__(self):
        return f"<Region id={self.guid} title_uz='{self.title_uz}'>"


class District(BaseModel):
    """Tuman yoki shahar — viloyat ichidagi maʼmuriy birlik (filter uchun)."""

    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        related_name="districts",
        verbose_name=_("Region"),
    )
    title_uz = models.CharField(max_length=100, verbose_name=_("Title (uz)"))
    title_ru = models.CharField(max_length=100, verbose_name=_("Title (ru)"))
    title_en = models.CharField(max_length=100, verbose_name=_("Title (en)"))

    class Meta:
        verbose_name = _("District")
        verbose_name_plural = _("Districts")
        ordering = ["region", "title_uz"]
        constraints = [
            models.UniqueConstraint(
                fields=["region", "title_uz"],
                name="unique_region_district_uz",
            )
        ]

    def __str__(self):
        return f"{self.title_uz} ({self.region.title_uz})"

    def __repr__(self):
        return f"<District id={self.guid} region={self.region_id} title_uz='{self.title_uz}'>"

class VerificationStatus(models.TextChoices):
    WAITING = ("waiting", _("Waiting"))
    ACCEPTED = ("accepted", _("Accepted"))
    CANCELLED = ("cancelled", _("Cancelled"))


class Property(HardDeleteBaseModel, VerifiedByMixin):
    verification_status = models.CharField(
        max_length=10,
        choices=VerificationStatus,
        default=VerificationStatus.WAITING,
        db_default=VerificationStatus.WAITING,
        verbose_name=_("Verification status"),
    )
    title = models.CharField(max_length=75, verbose_name=_("Title"))
    title_sort = models.CharField(
        max_length=255,
        default="",
        blank=True,
        verbose_name=_("Title (for sorting)"),
        help_text=_("Filled automatically from title if empty."),
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name=_("Price")
    )
    currency = models.CharField(
        max_length=3,
        choices=Currency,
        default=Currency.USD,
        db_default=Currency.USD,
        verbose_name=_("Currency"),
    )
    property_type = models.ForeignKey(
        PropertyType,
        on_delete=models.CASCADE,
        related_name="properties",
        verbose_name=_("Property type"),
    )
    property_location = models.OneToOneField(
        PropertyLocation,
        on_delete=models.CASCADE,
        related_name="property",
        verbose_name=_("Property location"),
    )
    region = models.ForeignKey(
        Region,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="properties",
        verbose_name=_("Region"),
    )
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="properties",
        verbose_name=_("District"),
    )
    property_services = models.ManyToManyField(
        PropertyService,
        related_name="properties",
        verbose_name=_("Property services"),
    )
    categories = models.ManyToManyField(
        Category,
        related_name="properties",
        verbose_name=_("Categories (Свежие предложения)"),
        blank=True,
    )
    comment_count = models.PositiveIntegerField(
        default=0, db_default=0, verbose_name=_("Comment count")
    )
    minimum_weekend_day_stay = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Minimum weekend day stay"),
    )
    # If True: check-in only Friday or Saturday, and the stay must include Sunday (check-out Monday or later).
    weekend_only_sunday_inclusive = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Weekend only, Sunday inclusive"),
        help_text=_(
            "If set, customers can only book with check-in on Friday or Saturday, "
            "and the stay must include Sunday (check-out on Monday or later)."
        ),
    )
    partner = models.ForeignKey(
        Partner,
        related_name="partner",
        on_delete=models.CASCADE,
        verbose_name=_("Partner"),
    )
    is_archived = models.BooleanField(
        default=False, db_default=False, verbose_name=_("Is archived")
    )
    is_recommended = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Recommended"),
        help_text=_("Show in «Recommended places» block on main page"),
    )
    img = models.ImageField(
        upload_to="property/covers/",
        verbose_name=_("Cover image"),
        blank=True,
        null=True,
    )

    objects = PropertyManager()

    class Meta:
        verbose_name = _("Property")
        verbose_name_plural = _("Properties")
        constraints = [
            models.CheckConstraint(
                check=models.Q(price__gte=0),
                name="price_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(price__lt=9999999999.99),
                name="price_max_digits",
            ),
            models.UniqueConstraint(
                fields=["title"],
                condition=models.Q(is_archived=False),
                name="unique_active_title",
            ),
            models.UniqueConstraint(
                fields=["property_location"],
                condition=models.Q(is_archived=False),
                name="unique_active_property_location",
            ),
        ]

    def save(self, *args, **kwargs):
        self.is_verified = self.verification_status == VerificationStatus.ACCEPTED
        if not self.title_sort and self.title:
            self.title_sort = self.title
        super().save(*args, **kwargs)

    def archive(self):
        self.is_archived = True
        self.save(update_fields=["is_archived", "is_verified", "verification_status"])

    def __str__(self):
        return self.title

    def __repr__(self):
        return f"<Property id={self.guid} title='{self.title}'"


class Apartment(Property):
    """Proxy model: admin panelda faqat Apartment tipidagi propertylarni ko'rsatish uchun."""

    class Meta:
        proxy = True
        verbose_name = _("Apartment")
        verbose_name_plural = _("Apartments")


class Cottages(Property):
    """Proxy model: admin panelda faqat Cottages tipidagi propertylarni ko'rsatish uchun."""

    class Meta:
        proxy = True
        verbose_name = _("Cottage")
        verbose_name_plural = _("Cottages")


class PropertyPrice(HardDeleteBaseModel):
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="property_price",
        verbose_name=_("Property"),
    )
    month_from = models.DateField(
        verbose_name=_("Price valid from"), help_text=_("Enter current month first day")
    )
    month_to = models.DateField(
        verbose_name=_("Price valid to"), help_text=_("Enter current month last day")
    )
    price_per_person = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("Price per person"),
        help_text=_("Price for addition person"),
    )
    price_on_working_days = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Price on working days")
    )
    price_on_weekends = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name=_("Price on weekends")
    )

    class Meta:
        verbose_name = _("Property price")
        verbose_name_plural = _("Property prices")
        constraints = [
            models.CheckConstraint(
                check=models.Q(price_per_person__gte=0),
                name="price_per_person_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(price_on_working_days__gte=0),
                name="price_on_working_days_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(price_on_weekends__gte=0),
                name="price_on_weekends_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(price_per_person__lte=9999999999.99),
                name="price_per_person_max_digits",
            ),
            models.CheckConstraint(
                check=models.Q(price_on_working_days__lte=9999999999.99),
                name="price_on_working_days_max_digits",
            ),
            models.CheckConstraint(
                check=models.Q(price_on_weekends__lte=9999999999.99),
                name="price_on_weekends_max_digits",
            ),
        ]

    def __str__(self):
        return f"Prices for {self.property.title}"

    def __repr__(self):
        return f"<PropertyPrice id={self.guid} property_id={self.property.guid}"


class PropertyImage(HardDeleteBaseModel):
    def _upload_directory_path(self, filename: str) -> str:
        extension = filename.split(".")[-1]
        unique_name = uuid.uuid4().hex
        return f"property/images/{unique_name}.{extension}"

    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="property_images",
        verbose_name=_("Property"),
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

    def clean(self):
        if self.image:
            if self.image.size > settings.MAX_IMAGE_SIZE:
                raise ValidationError(
                    {
                        "image": _("Image file too large, maximum size is 20MB"),
                    }
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

    class Meta:
        verbose_name = _("Property image")
        verbose_name_plural = _("Property images")
        ordering = ["order"]

    def __str__(self):
        return f"Image for {self.property.title}"

    def __repr__(self):
        return f"<PropertyImage id={self.guid} property_id={self.property_id}> order={self.order}"


class PropertyDetail(HardDeleteBaseModel):
    property = models.OneToOneField(
        Property,
        on_delete=models.CASCADE,
        related_name="property_detail",
        verbose_name=_("Property"),
    )
    apartment_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Apartment number"),
    )
    home_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Home number"),
    )
    entrance_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Entrance number"),
    )
    floor_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Floor number"),
    )
    pass_code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name=_("Pass code"),
    )
    description_en = models.TextField(verbose_name=_("Description (en)"))
    description_ru = models.TextField(verbose_name=_("Description (ru)"))
    description_uz = models.TextField(verbose_name=_("Description (uz)"))
    check_in = models.TimeField(default=time(19, 0), verbose_name=_("Check in"))
    check_out = models.TimeField(default=time(17, 0), verbose_name=_("Check out"))
    is_allowed_alcohol = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Is allowed alcohol"),
    )
    is_allowed_corporate = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Is allowed corporate"),
    )
    is_allowed_pets = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Is allowed pets"),
    )
    is_quiet_hours = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name=_("Is quiet hours"),
    )

    class Meta:
        verbose_name = _("Property detail")
        verbose_name_plural = _("Property details")

    def __str__(self):
        return f"Detail of {self.property.title}"

    def __repr__(self):
        return f"PropertyDetail id={self.guid} property_id={self.property_id}"


class PropertyRoom(HardDeleteBaseModel):
    property = models.OneToOneField(
        Property,
        on_delete=models.CASCADE,
        related_name="property_room",
        verbose_name=_("Property"),
    )

    guests = models.PositiveIntegerField(
        default=1, db_default=1, verbose_name=_("Guests")
    )
    rooms = models.PositiveIntegerField(
        default=1, db_default=1, verbose_name=_("Rooms")
    )
    beds = models.PositiveIntegerField(default=1, db_default=1, verbose_name=_("Beds"))
    bathrooms = models.PositiveIntegerField(
        default=1, db_default=1, verbose_name=_("Bathrooms")
    )

    class Meta:
        verbose_name = _("Property room")
        verbose_name_plural = _("Property rooms")

    def __str__(self):
        return f"Rooms for {self.property.title}"

    def __repr__(self):
        return f"PropertyRoom id={self.guid} property_id={self.property.guid}"


class PropertyReview(HardDeleteBaseModel):
    client = models.ForeignKey(
        Client,
        related_name="client",
        on_delete=models.CASCADE,
        verbose_name=_("Client"),
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="property_review",
        verbose_name=_("Property"),
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
        null=True, blank=True, default=False, db_default=False, verbose_name=_("Hide")
    )

    class Meta:
        verbose_name = _("Property review")
        verbose_name_plural = _("Property reviews")

    def __str__(self):
        return f"Review by {self.client} for {self.property}"

    def __repr__(self):
        return f"<PropertyReview id={self.guid} client={self.client} rating={self.rating} is_hidden={self.is_hidden}>"


class PropertyFavorite(HardDeleteBaseModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="property_favorites",
        verbose_name=_("Client"),
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="favorites",
        verbose_name=_("Property"),
    )

    class Meta:
        verbose_name = _("Property favorite")
        verbose_name_plural = _("Property favorites")
        constraints = [
            models.UniqueConstraint(
                fields=["client", "property"],
                name="unique_client_property_favorite",
            )
        ]

    def __str__(self):
        return f"{self.client} ♥ {self.property}"


@receiver(post_save, sender=Property)
def update_pending_property_images(sender, instance: Property, created=False, **kwargs):
    previously_verified = getattr(instance, "_previous_is_verified", None)
    became_verified = bool(instance.is_verified) and not bool(previously_verified)

    if became_verified:
        PropertyImage.objects.filter(
            property=instance,
            is_pending=True,
        ).update(is_pending=False)


@receiver(post_save, sender=Property)
def sync_property_prices_from_property(sender, instance: Property, created=False, **kwargs):
    """
    Keep PropertyPrice rows aligned with scalar Property.price.
    We use update() to avoid recursive signal loops.
    """
    if instance.price is None:
        return

    previous_price = getattr(instance, "_previous_price", None)
    if not created and previous_price == instance.price:
        return

    updated_rows = instance.property_price.update(
        price_on_working_days=instance.price,
        price_on_weekends=instance.price,
    )

    if updated_rows:
        return

    current_month_start = month_start(timezone.localdate())
    PropertyPrice.objects.create(
        property=instance,
        month_from=current_month_start,
        month_to=month_end(current_month_start),
        price_per_person=Decimal("0"),
        price_on_working_days=instance.price,
        price_on_weekends=instance.price,
    )


@receiver(pre_save, sender=Property)
def cache_property_verification_state(sender, instance: Property, **kwargs):
    if not instance.pk:
        instance._previous_is_verified = None
        instance._previous_price = None
        return

    previous_state = (
        sender.objects.filter(pk=instance.pk)
        .values("is_verified", "price")
        .first()
    )
    if previous_state:
        instance._previous_is_verified = previous_state["is_verified"]
        instance._previous_price = previous_state["price"]
        return

    instance._previous_is_verified = None
    instance._previous_price = None


@receiver(post_save, sender=PropertyPrice)
def sync_property_from_property_price(sender, instance: PropertyPrice, **kwargs):
    """
    Keep scalar Property.price aligned when any monthly price row changes.
    Also keep all monthly rows unified so admin edits update a single logical price.
    """
    new_price = instance.price_on_working_days

    if instance.price_on_weekends != new_price:
        PropertyPrice.objects.filter(pk=instance.pk).update(price_on_weekends=new_price)

    PropertyPrice.objects.filter(property=instance.property).exclude(pk=instance.pk).update(
        price_on_working_days=new_price,
        price_on_weekends=new_price,
    )

    Property.objects.filter(pk=instance.property_id).update(price=new_price)


@receiver([post_save], sender=PropertyReview)
def update_property_comment_count_on_create(
    instance: PropertyReview, created=False, **kwargs
):
    if created:
        Property.objects.filter(guid=instance.property.guid).update(
            comment_count=models.F("comment_count") + 1
        )


@receiver(post_delete, sender=PropertyReview)
def update_property_comment_count_on_delete(instance, **kwargs):
    Property.objects.filter(guid=instance.property.guid).update(
        comment_count=models.F("comment_count") - 1
    )
