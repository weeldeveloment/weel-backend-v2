from django.db import models
from django.db.models.functions import Lower
from django.core.validators import RegexValidator, FileExtensionValidator
from django.utils.translation import gettext_lazy as _

from django.contrib.auth import get_user_model

from shared.models import HardDeleteBaseModel, VerifiedByMixin
from shared.utility import USERNAME_REGEX, PHONE_NUMBER_REGEX, USER_NAME_REGEX

from core import settings

User = get_user_model()

PARTNER_NAME_REGEX_VALIDATOR = RegexValidator(
    regex=USER_NAME_REGEX,
    message="Firstname and Lastname must be 3–64 characters long",
)


class DocumentType(models.TextChoices):
    CERTIFICATE = "CERT", _("Certificate")
    PASSPORT = "PASS", _("Passport")


class Partner(HardDeleteBaseModel, VerifiedByMixin):
    first_name = models.CharField(
        max_length=255, validators=[PARTNER_NAME_REGEX_VALIDATOR], verbose_name=_("First name")
    )
    last_name = models.CharField(
        max_length=255, validators=[PARTNER_NAME_REGEX_VALIDATOR], verbose_name=_("Last name")
    )
    username = models.CharField(
        max_length=255,
        unique=False,
        db_index=True,
        validators=[
            RegexValidator(
                regex=USERNAME_REGEX, message="Username must be 3–32 characters long"
            )
        ],
        verbose_name=_("Username"),
    )
    phone_number = models.CharField(
        max_length=16,
        unique=True,
        db_index=True,
        validators=[
            RegexValidator(
                regex=PHONE_NUMBER_REGEX, message="Phone number should start with +998"
            )
        ],
        verbose_name=_("Phone number"),
    )
    email = models.EmailField(null=True, blank=True, verbose_name=_("Email"))
    avatar = models.FileField(
        null=True,
        blank=True,
        upload_to="users/avatars/",
        validators=[FileExtensionValidator(settings.ALLOWED_PHOTO_EXTENSION)],
    )
    is_email_verified = models.BooleanField(null=True, blank=True, verbose_name=_("Email verified"))
    is_active = models.BooleanField(default=True, db_default=True, verbose_name=_("Active"))
    # password = models.CharField(max_length=128)

    class Meta:
        verbose_name = _("Partner")
        verbose_name_plural = _("Partners")
        constraints = [
            models.UniqueConstraint(Lower("username"), name="unique_username_ci")
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} (@{self.username})"

    def __repr__(self):
        return (
            f"<Partner id={self.id} username={self.username} phone={self.phone_number}>"
        )


class PartnerTelegramUser(HardDeleteBaseModel):
    partner = models.OneToOneField(
        Partner, on_delete=models.CASCADE, related_name="telegram", null=True, blank=True, verbose_name=_("Partner")
    )
    telegram_user_id = models.BigIntegerField(
        unique=True,
        db_index=True,
    )
    username = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))

    class Meta:
        verbose_name = _("Partner Telegram user")
        verbose_name_plural = _("Partner Telegram users")

    def __str__(self):
        return f"{self.partner} → {self.telegram_user_id}"

class PartnerSession(HardDeleteBaseModel):
    partner = models.ForeignKey(Partner, on_delete=models.SET_NULL, null=True, verbose_name=_("Partner"))
    device_id = models.CharField(null=True, blank=True, verbose_name=_("Device ID"))
    user_agent = models.CharField(verbose_name=_("User agent"))
    last_ip = models.GenericIPAddressField(verbose_name=_("Last IP"))

    class Meta:
        verbose_name = _("Partner session")
        verbose_name_plural = _("Partner sessions")

    def __str__(self):
        return f"Session for {self.partner} from {self.last_ip}"

    def __repr__(self):
        return f"<PartnerSession id={self.id} partner_id={self.partner_id} ip={self.last_ip}>"


class PartnerDevice(HardDeleteBaseModel):
    class PartnerDeviceType(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="devices",
        verbose_name=_("Partner"),
    )
    fcm_token = models.CharField(max_length=255, unique=True, verbose_name=_("FCM token"))
    device_type = models.CharField(
        max_length=10,
        choices=PartnerDeviceType,
        verbose_name=_("Device type"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))

    class Meta:
        db_table = "partner_devices"
        verbose_name = _("Partner device")
        verbose_name_plural = _("Partner devices")

    def __str__(self):
        return f"Partner: {self.partner} | Device: {self.device_type}"

    def __repr__(self):
        return f"<PartnerDevice id={self.id} partner_id={self.partner_id}> device_type={self.device_type} is_active={self.is_active}"

class PartnerDocument(HardDeleteBaseModel, VerifiedByMixin):
    partner = models.ForeignKey(Partner, on_delete=models.SET_NULL, null=True, verbose_name=_("Partner"))
    document = models.FileField(
        upload_to="users/documents/",
        validators=[FileExtensionValidator(allowed_extensions=["pdf", "jpg", "png"])],
        verbose_name=_("Document"),
    )
    type = models.CharField(
        max_length=4,
        choices=DocumentType.choices,
        verbose_name=_("Type"),
    )
    is_verified = models.BooleanField(null=True, blank=True, verbose_name=_("Is verified"))
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"is_staff": True},
        verbose_name=_("Verified by"),
    )
    verified_at = models.DateTimeField(blank=True, null=True, verbose_name=_("Verified at"))

    class Meta:
        verbose_name = _("Partner document")
        verbose_name_plural = _("Partner documents")

    def __str__(self):
        return f"{self.get_type_display()} for {self.partner}"

    def __repr__(self):
        return f"<PartnerDocument id={self.id} partner_id={self.partner_id} type={self.type}>"
