"""
Log full exception tracebacks for 500 errors.
Adds detailed error logging + Prometheus counter + trace correlation.
"""
import logging
import traceback

from core.telemetry import get_trace_context

logger = logging.getLogger("django.request")

# Prometheus counter for unhandled exceptions (lazy init to avoid import-time deps)
_exception_counter = None


def _get_exception_counter():
    global _exception_counter
    if _exception_counter is not None:
        return _exception_counter
    try:
        from prometheus_client import Counter
        _exception_counter = Counter(
            "django_unhandled_exceptions_total",
            "Unhandled exceptions by type and path",
            ["exception_type", "path"],
        )
    except Exception:
        pass
    return _exception_counter


class ExceptionLoggingMiddleware:
    """
    Middleware that catches unhandled exceptions and logs them with full
    traceback before Django's default handling. Also exports a Prometheus
    counter for Grafana alerting.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Called when a view raises an exception. Log full traceback + metric."""
        tb = traceback.format_exc()
        exc_type = type(exception).__name__
        path = request.path or "/"

        # Prometheus counter
        counter = _get_exception_counter()
        if counter is not None:
            counter.labels(exception_type=exc_type, path=path).inc()

        # Trace context for Loki -> Tempo correlation
        trace_ctx = get_trace_context()
        extra = {"request": request}
        if trace_ctx.get("trace_id"):
            extra["trace_id"] = trace_ctx["trace_id"]
        if trace_ctx.get("span_id"):
            extra["span_id"] = trace_ctx["span_id"]

        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method,
            path,
            exception,
            tb,
            exc_info=False,  # We already have tb above
            extra=extra,
        )
        return None  # Let Django handle the exception normally
