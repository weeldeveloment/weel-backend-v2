"""
Custom CORS middleware to allow ngrok-free.app origins dynamically.
This handles cases where django-cors-headers doesn't support regex patterns
or where we need more flexible origin matching.
"""

import re
from django.utils.deprecation import MiddlewareMixin


class NgrokCorsMiddleware(MiddlewareMixin):
    """
    Adds CORS headers for ngrok-free.app origins when DEBUG=True.
    Works alongside django-cors-headers.
    """

    def process_response(self, request, response):
        # Only apply in DEBUG mode (development with ngrok)
        if not getattr(request, "DEBUG", False):
            return response

        origin = request.META.get("HTTP_ORIGIN", "")
        if not origin:
            return response

        # Check if origin matches ngrok-free.app pattern
        ngrok_pattern = re.compile(r"^https?://.*\.ngrok-free\.app$", re.IGNORECASE)
        if ngrok_pattern.match(origin):
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
            response["Access-Control-Allow-Methods"] = "DELETE, GET, OPTIONS, PATCH, POST, PUT"
            response["Access-Control-Allow-Headers"] = (
                "accept, accept-encoding, authorization, "
                "content-type, origin, x-csrftoken, x-telegram-initdata"
            )

        return response
