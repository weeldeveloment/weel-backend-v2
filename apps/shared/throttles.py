from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


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
        return super().allow_request(request, view)


class SwaggerExemptUserRateThrottle(UserRateThrottle):
    """
    Keep global user throttling, but skip Swagger/OpenAPI docs endpoints.
    """

    def allow_request(self, request, view):
        if request.path.startswith("/swagger/"):
            return True
        return super().allow_request(request, view)
