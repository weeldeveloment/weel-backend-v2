from django.db import models
from django.utils.translation import gettext_lazy as _


class Currency(models.TextChoices):
    USD = "USD", _("US Dollar")
    UZS = "UZS", _("Uzbekistan So'm")

