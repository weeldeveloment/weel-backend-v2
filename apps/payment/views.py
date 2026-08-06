from datetime import date

from django.core.cache import cache

from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .exchange_rate import exchange_rate


class ExchangeRateView(APIView):
    """Returns the current USD-to-UZS exchange rate."""

    # Public reference data — shown on the pricing screens before login.
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        try:
            rate = exchange_rate()
            rate_date = cache.get("usd_to_uzs_rate_date") or str(date.today())
        except Exception:
            return Response(
                {"rate": None, "error": "Exchange rate not available"},
                status=503,
            )
        return Response({
            "rate": str(rate),
            "date": rate_date,
        })
