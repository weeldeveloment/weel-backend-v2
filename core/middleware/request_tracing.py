import logging
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

from core.telemetry import get_trace_context

logger = logging.getLogger("core.request_tracing")


class RequestTracingMiddleware(MiddlewareMixin):
    """Logs every request/response with timing, user, org, and correlation IDs."""

    def process_request(self, request):
        request_id = str(uuid.uuid4())
        request._request_id = request_id
        request._start_time = time.monotonic()

        trace_ctx = get_trace_context()
        extra = {
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "remote_addr": _get_client_ip(request),
        }
        extra.update(trace_ctx)

        logger.info("request_started", extra=extra)

    def process_response(self, request, response):
        request_id = getattr(request, "_request_id", "")
        start_time = getattr(request, "_start_time", None)

        duration_ms = 0.0
        if start_time is not None:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None) if user else None
        org = getattr(request, "organization", None)
        org_id = None
        if org:
            org_id = org.get("id") if isinstance(org, dict) else getattr(org, "id", None)
        if org_id is None and user:
            org_id = getattr(user, "organization_id", None)

        trace_ctx = get_trace_context()
        extra = {
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "user_id": user_id,
            "organization_id": org_id,
        }
        extra.update(trace_ctx)

        if response.status_code >= 500:
            log_level = logging.ERROR
        elif response.status_code >= 400:
            log_level = logging.WARNING
        else:
            log_level = logging.INFO

        logger.log(log_level, "request_completed", extra=extra)

        if request_id:
            response["X-Request-ID"] = request_id

        return response

    def process_exception(self, request, exception):
        request_id = getattr(request, "_request_id", "")
        start_time = getattr(request, "_start_time", None)

        duration_ms = 0.0
        if start_time is not None:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None) if user else None

        trace_ctx = get_trace_context()
        extra = {
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status_code": 500,
            "duration_ms": duration_ms,
            "user_id": user_id,
            "exception_type": type(exception).__name__,
        }
        extra.update(trace_ctx)

        logger.error(
            "request_failed: %s",
            exception,
            extra=extra,
            exc_info=False,
        )


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
