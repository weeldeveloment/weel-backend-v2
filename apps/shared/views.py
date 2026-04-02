from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
import logging
from datetime import datetime, timezone

logger = logging.getLogger("frontend")


class FrontendLogView(APIView):
    """Frontend (brauzer) loglarini qabul qiladi – Grafana/Loki da ko'rsatiladi."""
    permission_classes = [AllowAny]
    throttle_scope = "frontend_log"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
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
