from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel

from .choices import Currency


class PlumTransactionType(ChoiceEnum):
    HOLD = "HOLD"
    CHARGE = "CHRG"


PlumTransactionType.choices = build_choices(PlumTransactionType)


class PlumTransactionStatus(ChoiceEnum):
    PENDING = "PENDING"
    HOLD_CONFIRMED = "HOLD_CONFIRMED"
    CHARGED = "CHARGED"
    DISMISSED = "DISMISSED"
    FAILED = "FAILED"


PlumTransactionStatus.choices = build_choices(PlumTransactionStatus)


@dataclass(slots=True)
class PlumTransaction(HardDeleteBaseModel):
    transaction_id: str | None = None
    hold_id: str | None = None
    amount: Decimal | None = None
    type: str = PlumTransactionType.HOLD.value
    status: str = PlumTransactionStatus.PENDING.value
    card_id: str | None = None
    extra_id: str | None = None

    _meta = SimpleNamespace(db_table="payment_plumtransaction")


@dataclass(slots=True)
class ExchangeRate(HardDeleteBaseModel):
    currency: str = Currency.USD.value
    rate: Decimal | None = None
    date: date | None = None

    _meta = SimpleNamespace(db_table="payment_exchangerate")
