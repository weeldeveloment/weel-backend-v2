import json
import logging
import time

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


logger = logging.getLogger(__name__)

_DEBUG_LOG_PATH = "/home/abbbose/projects/protouch/weel-backend-v2/.cursor/debug-ce4097.log"

_SWAGGER_PREFIXES = (
    "/swagger/",
    "/api/swagger/",
    "/redoc/",
    "/api/redoc/",
)


# #region agent log
def _debug_log(hypothesis_id, location, message, data=None):
    try:
        entry = json.dumps({"sessionId": "ce4097", "hypothesisId": hypothesis_id, "location": location, "message": message, "data": data or {}, "timestamp": int(time.time() * 1000)})
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass
# #endregion


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
        if request.path.startswith(_SWAGGER_PREFIXES):
            return True
        try:
            allowed = super().allow_request(request, view)
            # #region agent log
            if not allowed:
                ident = self.get_ident(request)
                _debug_log("H1", "throttles.py:anon", "ANON_THROTTLED", {"path": request.path, "ident": ident, "rate": self.rate, "num_requests": getattr(self, "num_requests", None)})
            # #endregion
            return allowed
        except Exception as exc:
            logger.warning("Anon throttle cache unavailable; allowing request: %s", exc)
            # #region agent log
            _debug_log("H1", "throttles.py:anon_exc", "ANON_THROTTLE_EXCEPTION", {"path": request.path, "error": str(exc)})
            # #endregion
            return True


class SwaggerExemptUserRateThrottle(UserRateThrottle):
    """
    Keep global user throttling, but skip Swagger/OpenAPI docs endpoints.
    """

    def allow_request(self, request, view):
        if request.path.startswith(_SWAGGER_PREFIXES):
            return True
        try:
            allowed = super().allow_request(request, view)
            # #region agent log
            if not allowed:
                _debug_log("H2", "throttles.py:user", "USER_THROTTLED", {"path": request.path, "rate": self.rate})
            # #endregion
            return allowed
        except Exception as exc:
            logger.warning("User throttle cache unavailable; allowing request: %s", exc)
            # #region agent log
            _debug_log("H2", "throttles.py:user_exc", "USER_THROTTLE_EXCEPTION", {"path": request.path, "error": str(exc)})
            # #endregion
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
