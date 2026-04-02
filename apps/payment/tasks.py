from decimal import Decimal
from typing import Any
import logging

from celery import shared_task
from django.core.cache import cache
import requests

from core import settings

logger = logging.getLogger(__name__)


def _extract_usd_to_uzs_rate(payload: Any) -> Decimal:
    # open.er-api.com format: {"result": "success", "rates": {"UZS": 12892.04}}
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

    # Legacy CBU format: [{"Ccy": "USD", "Rate": "12892,04"}, ...]
    if isinstance(payload, list):
        for item in payload:
            if item.get("Ccy") == "USD" and item.get("Rate"):
                return Decimal(str(item["Rate"]).replace(",", "."))

    raise ValueError("USD to UZS rate not found in exchange API response")


@shared_task
def update_exchange_rate():
    response = requests.get(settings.CURRENT_CURRENCY_EXCHANGE_RATE, timeout=5)
    response.raise_for_status()
    rate = _extract_usd_to_uzs_rate(response.json())

    cache.set("usd_to_uzs_rate", rate, timeout=86400)  # 86400 - 24 hours
