"""
ASGI config for core project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""
       
import os
from urllib.parse import urlparse
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.http.request import split_domain_port, validate_host

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import after Django setup
from chat.routing import websocket_urlpatterns


def _header_value(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            try:
                return value.decode("latin1")
            except Exception:
                return ""
    return ""


class OptionalOriginAllowedHostsValidator:
    """
    Validate websocket Origin when present.
    If Origin header is missing (common in non-browser clients like Postman),
    allow the connection and rely on token authentication in consumer.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            return await self.app(scope, receive, send)

        origin = _header_value(scope, b"origin").strip()
        if not origin:
            return await self.app(scope, receive, send)

        parsed = urlparse(origin)
        host = (parsed.netloc or "").strip()
        if not host:
            await send({"type": "websocket.close", "code": 1008})
            return

        domain, _port = split_domain_port(host)
        if not validate_host(domain, settings.ALLOWED_HOSTS):
            await send({"type": "websocket.close", "code": 1008})
            return

        return await self.app(scope, receive, send)

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": OptionalOriginAllowedHostsValidator(
        URLRouter(websocket_urlpatterns)
    ),
})
      
