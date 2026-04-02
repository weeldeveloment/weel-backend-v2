"""
Log full exception tracebacks for 500 errors.
Adds detailed error logging to help debug production issues.
"""
import logging
import traceback

logger = logging.getLogger("django.request")


class ExceptionLoggingMiddleware:
    """
    Middleware that catches unhandled exceptions and logs them with full
    traceback before Django's default handling. This ensures we always
    have complete error details in production logs.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Called when a view raises an exception. Log full traceback."""
        tb = traceback.format_exc()
        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method,
            request.path,
            exception,
            tb,
            exc_info=False,  # We already have tb above
            extra={"request": request},
        )
        return None  # Let Django handle the exception normally
