from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import RegexValidator, FileExtensionValidator

from shared.models import HardDeleteBaseModel
from shared.utility import USERNAME_REGEX, PHONE_NUMBER_REGEX, USER_NAME_REGEX

from core import settings

CLIENT_NAME_REGEX_VALIDATOR = RegexValidator(
    regex=USER_NAME_REGEX,
    message="Firstname and Lastname must be 3–64 characters long",
)


class Client(HardDeleteBaseModel):
    first_name = models.CharField(
        max_length=255, validators=[CLIENT_NAME_REGEX_VALIDATOR], verbose_name=_("First name")
    )
    last_name = models.CharField(
        max_length=255, validators=[CLIENT_NAME_REGEX_VALIDATOR], verbose_name=_("Last name")
    )
    # username = models.CharField(
    #     max_length=255,
    #     unique=True,
    #     db_index=True,
    #     validators=[
    #         RegexValidator(
    #             regex=USERNAME_REGEX, message="Username must be 3–32 characters long"
    #         )
    #     ],
    # )
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
    avatar = models.FileField(
        null=True,
        blank=True,
        upload_to="users/avatars/",
        validators=[FileExtensionValidator(settings.ALLOWED_PHOTO_EXTENSION)],
    )
    is_active = models.BooleanField(default=True, db_default=True, verbose_name=_("Active"))

    class Meta:
        verbose_name = _("Client")
        verbose_name_plural = _("Clients")

    def __str__(self):
        return f"{self.first_name}: {self.last_name}"

    def __repr__(self):
        return (
            f"<Client id={self.id}"
            f"phone={self.phone_number!r} active={self.is_active}>"
        )

class ClientSession(HardDeleteBaseModel):
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, verbose_name=_("Client"))
    device_id = models.CharField(null=True, blank=True, verbose_name=_("Device ID"))
    user_agent = models.CharField(verbose_name=_("User agent"))
    last_ip = models.GenericIPAddressField(verbose_name=_("Last IP"))

    class Meta:
        verbose_name = _("Client session")
        verbose_name_plural = _("Client sessions")

    def __str__(self):
        return f"Session {self.id} for {self.client.first_name if self.client else 'Unknown'}"

    def __repr__(self):
        return (
            f"<ClientSession id={self.id} client_id={self.client_id} "
            f"device_id={self.device_id!r} ip={self.last_ip}>"
        )


class ClientDevice(HardDeleteBaseModel):
    class ClientDeviceType(models.TextChoices):
        IOS = "ios", "iOS"
        ANDROID = "android", "Android"

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="devices",
        verbose_name=_("Client"),
    )
    fcm_token = models.CharField(max_length=255, unique=True, verbose_name=_("FCM token"))
    device_type = models.CharField(
        max_length=10,
        choices=ClientDeviceType,
        verbose_name=_("Device type"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))

    class Meta:
        db_table = "client_devices"
        verbose_name = _("Client device")
        verbose_name_plural = _("Client devices")

    def __str__(self):
        return f"Client: {self.client} | Device: {self.device_type}"

    def __repr__(self):
        return f"<ClientDevice id={self.id} client_id={self.client_id}> device_type={self.device_type} is_active={self.is_active}"
