from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
import logging
from datetime import datetime, timezone
from django.conf import settings
from .throttles import ResilientScopedRateThrottle

logger = logging.getLogger("frontend")


class FrontendLogView(APIView):
    """Frontend (brauzer) loglarini qabul qiladi – Grafana/Loki da ko'rsatiladi."""
    permission_classes = [AllowAny]
    throttle_scope = "frontend_log"
    throttle_classes = [ResilientScopedRateThrottle]

    def post(self, request):
        expected_token = getattr(settings, "FRONTEND_LOG_TOKEN", "")
        provided = (request.headers.get("X-Frontend-Log-Token") or "").strip()
        if expected_token and provided != expected_token:
            return Response(
                {"detail": "Invalid log token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        level = (request.data.get("level") or "info").lower()
        message = request.data.get("message") or ""
        extra = dict(request.data.get("extra") or {})
        extra["timestamp"] = datetime.now(timezone.utc).isoformat()
        extra["level"] = level
        if request.data.get("url"):
            extra["url"] = request.data.get("url")
        if request.data.get("user_id"):
            extra["user_id"] = str(request.data.get("user_id"))
        log_method = getattr(logger, level, logger.info)
        log_method(message, extra=extra)
        return Response({"ok": True}, status=status.HTTP_201_CREATED)
