from django.db import models
from django.utils.translation import gettext_lazy as _

from shared.models import HardDeleteBaseModel
from users.models.clients import Client
from .choices import Currency


class PlumTransactionType(models.TextChoices):
    HOLD = "HOLD", "Hold"
    CHARGE = "CHRG", "Charge"


class PlumTransactionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    HOLD_CONFIRMED = "HOLD_CONFIRMED", "Hold Confirmed"
    CHARGED = "CHARGED", "Charged"
    DISMISSED = "DISMISSED", "Dismissed"
    FAILED = "FAILED", "Failed"


class PlumTransaction(HardDeleteBaseModel):
    transaction_id = models.CharField(max_length=255, unique=True)
    hold_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    type = models.CharField(
        max_length=4,
        choices=PlumTransactionType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=PlumTransactionStatus.choices,
        default=PlumTransactionStatus.PENDING,
    )
    card_id = models.CharField(max_length=255, null=True, blank=True)
    extra_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return (
            f"<Transaction id: {self.transaction_id} | "
            f"Hold id: {self.hold_id} | "
            f"Type: {self.get_type_display()} | "
            f"Status: {self.get_status_display()}>"
        )


class ExchangeRate(HardDeleteBaseModel):
    currency = models.CharField(
        max_length=3,
        choices=Currency,
        default=Currency.USD,
        db_default=Currency.USD,
        verbose_name=_("Currency"),
    )
    rate = models.DecimalField(max_digits=18, decimal_places=6, verbose_name=_("Rate"))
    date = models.DateField(auto_now=True, db_index=True, verbose_name=_("Date"))

    class Meta:
        verbose_name = _("Exchange rate")
        verbose_name_plural = _("Exchange rates")
        indexes = [
            models.Index(fields=["currency", "date"]),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["currency", "date"],
                name="unique_currency_date",
            ),
        ]

    def __str__(self):
        return f"Currency {self.currency!r}: {self.rate} on {self.date}"

    def __repr__(self):
        return f"<ExchangeRate id={self.guid} currency={self.currency} rate={self.rate} date={self.date}>"
