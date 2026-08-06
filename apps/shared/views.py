from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
import logging
from datetime import datetime, timezone
from django.conf import settings
from django.utils.crypto import constant_time_compare
from .throttles import ResilientScopedRateThrottle

logger = logging.getLogger("frontend")


class FrontendLogView(APIView):
    """Frontend (brauzer) loglarini qabul qiladi – Grafana/Loki da ko'rsatiladi."""
    permission_classes = [AllowAny]
    throttle_scope = "frontend_log"
    throttle_classes = [ResilientScopedRateThrottle]

    # Only real logging levels. `getattr(logger, <user input>)` used to reach
    # any attribute on the logger — `propagate`, `handlers`, `removeHandler` —
    # which either crashed with a 500 or touched logger internals.
    ALLOWED_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})

    # Cap on how much attacker-controlled text reaches the log pipeline.
    MAX_MESSAGE_LENGTH = 2000

    def post(self, request):
        expected_token = (getattr(settings, "FRONTEND_LOG_TOKEN", "") or "").strip()
        provided = (request.headers.get("X-Frontend-Log-Token") or "").strip()
        if not expected_token:
            # An unset token used to leave this endpoint wide open, letting
            # anyone flood app logs (and the Loki quota behind them).
            logger.warning("FRONTEND_LOG_TOKEN is not configured; rejecting log ingest")
            return Response(
                {"detail": "Frontend logging is not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not constant_time_compare(provided, expected_token):
            return Response(
                {"detail": "Invalid log token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        level = (request.data.get("level") or "info").lower()
        if level not in self.ALLOWED_LEVELS:
            level = "info"
        message = str(request.data.get("message") or "")[: self.MAX_MESSAGE_LENGTH]
        extra = dict(request.data.get("extra") or {})
        extra["timestamp"] = datetime.now(timezone.utc).isoformat()
        extra["level"] = level
        if request.data.get("url"):
            extra["url"] = request.data.get("url")
        if request.data.get("user_id"):
            extra["user_id"] = str(request.data.get("user_id"))
        getattr(logger, level)(message, extra=extra)
        return Response({"ok": True}, status=status.HTTP_201_CREATED)
