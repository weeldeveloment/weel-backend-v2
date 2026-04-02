from django.db import models
from django.core.validators import RegexValidator

from shared.models import HardDeleteBaseModel, BaseModel
from shared.utility import PHONE_NUMBER_REGEX


class SmsPurpose(models.TextChoices):
    LOGIN = ("CL_LGN", "Login")
    REGISTER = ("CL_RGR", "Register")
    PARTNER_LOGIN = ("PR_LGN", "Partner Login")
    PARTNER_REGISTER = ("PR_RGR", "Partner Register")
    PARTNER_PROPERTY_REMINDER = ("PR_RMD", "Partner Property Reminder")


class SmsLog(HardDeleteBaseModel):
    phone_number = models.CharField(
        max_length=16,
        db_index=True,
        validators=[
            RegexValidator(
                regex=PHONE_NUMBER_REGEX, message="Phone number should start with +998"
            )
        ],
    )
    purpose = models.CharField(max_length=6, choices=SmsPurpose.choices)
    is_sent = models.BooleanField(default=True, db_default=True)
