from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import ValidationError

from core import settings


def _extract_usd_to_uzs_rate(payload: Any) -> Decimal:
    if isinstance(payload, dict):
        result = payload.get("result")
        if result and result != "success":
            raise ValueError(f"Exchange API returned non-success result: {result}")
        if payload.get("success") is False:
            raise ValueError("Exchange API returned success=false")

        for rates_key in ("rates", "conversion_rates"):
            rates = payload.get(rates_key)
            if isinstance(rates, dict) and rates.get("UZS") is not None:
                return Decimal(str(rates["UZS"]))

    if isinstance(payload, list):
        for item in payload:
            if item.get("Ccy") == "USD" and item.get("Rate"):
                return Decimal(str(item["Rate"]).replace(",", "."))

    raise ValueError("USD to UZS rate not found in exchange API response")


def _fetch_live_rate() -> Decimal:
    response = requests.get(settings.CURRENT_CURRENCY_EXCHANGE_RATE, timeout=5)
    response.raise_for_status()
    return _extract_usd_to_uzs_rate(response.json())


def exchange_rate():
    rate = cache.get("usd_to_uzs_rate")
    if rate:
        return Decimal(str(rate))

    try:
        live_rate = _fetch_live_rate()
        cache.set("usd_to_uzs_rate", live_rate, timeout=86400)
        cache.set("usd_to_uzs_rate_date", str(date.today()), timeout=86400)
        return live_rate
    except Exception:
        raise ValidationError(_("Exchange rate isn't synchronized today"))


def round_amount(amount: Decimal) -> Decimal:
    """
    Round amount to nearst 10,000 UZS
    - last 4 digits < 5000  round down
    - last 4 digits ≥ 5000 round up
    """

    amount = amount.quantize(Decimal("1"))
    remainder = amount % Decimal("10000")

    if remainder < Decimal("5000"):
        return amount - remainder
    else:
        return amount + (Decimal("10000") - remainder)


def to_uzs(amount: Decimal) -> Decimal:
    rate = exchange_rate()
    return round_amount(amount * rate)


def to_usd(amount: Decimal) -> Decimal:
    rate = exchange_rate()
    return (amount / rate).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
