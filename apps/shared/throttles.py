from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class SwaggerExemptAnonRateThrottle(AnonRateThrottle):
    """
    Keep global anon throttling, but skip Swagger/OpenAPI docs endpoints.
    """

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
