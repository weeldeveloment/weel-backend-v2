import logging

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


logger = logging.getLogger(__name__)


class SwaggerExemptAnonRateThrottle(AnonRateThrottle):
    """
    Keep global anon throttling, but skip Swagger/OpenAPI docs endpoints.
    """

    def get_ident(self, request):
        """
        Prefer client IP from proxy headers so different users are not throttled
        as a single internal proxy IP.
        """
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return super().get_ident(request)

    def allow_request(self, request, view):
        if request.path.startswith("/swagger/"):
            return True
        try:
            return super().allow_request(request, view)
        except Exception as exc:
            logger.warning("Anon throttle cache unavailable; allowing request: %s", exc)
            return True


class SwaggerExemptUserRateThrottle(UserRateThrottle):
    """
    Keep global user throttling, but skip Swagger/OpenAPI docs endpoints.
    """

    def allow_request(self, request, view):
        if request.path.startswith("/swagger/"):
            return True
        try:
            return super().allow_request(request, view)
        except Exception as exc:
            logger.warning("User throttle cache unavailable; allowing request: %s", exc)
            return True


class ResilientScopedRateThrottle(ScopedRateThrottle):
    """
    Scoped throttle that fails open when cache backend is unavailable.
    Prevents auth/login endpoints from returning 500 during Redis outages.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception as exc:
            logger.warning("Scoped throttle cache unavailable; allowing request: %s", exc)
            return True
