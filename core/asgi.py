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

# Initialize OpenTelemetry tracing before Django app registry populates.
from core.telemetry import init_telemetry
init_telemetry()

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

# Import after Django setup
from chat.routing import websocket_urlpatterns
from apps.b2b.workspace.routing import (
    websocket_urlpatterns as workspace_websocket_urlpatterns,
)


def _header_value(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            try:
                return value.decode("latin1")
            except Exception:
                return ""
    return ""


class MetricsHostASGIMiddleware:
    """
    Prometheus scrapes the container by its Dokploy service name
    (`weel-devbackend-xyz:8000`), which is never in ALLOWED_HOSTS. Anything
    that calls request.get_host() before our Django middleware runs — the
    OpenTelemetry Django instrumentation inserts itself at MIDDLEWARE[0] —
    turns that into 400 DisallowedHost and a false BackendDown alert. So the
    Host header is fixed here, before Django ever builds the request: only for
    /metrics, only when the peer is a private address (the Docker network) or
    presents the metrics bearer token.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/metrics"):
            from core.middleware import metrics_guard as guard

            headers = list(scope.get("headers", []))
            hdr = {k: v for k, v in headers}
            client = (scope.get("client") or ("", 0))[0] or ""
            token = (getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()
            auth = hdr.get(b"authorization", b"").decode("latin1")
            authorized = guard._is_private(client) or (bool(token) and auth == f"Bearer {token}")
            host = hdr.get(b"host", b"").decode("latin1")
            domain, _port = split_domain_port(host)
            if authorized and not (domain and validate_host(domain, settings.ALLOWED_HOSTS)):
                substitute = guard._substitute_host().encode("latin1")
                headers = [(k, v) for k, v in headers if k not in (b"host", b"x-forwarded-host")]
                headers.append((b"host", substitute))
                scope = {**scope, "headers": headers}
        return await self.app(scope, receive, send)


class TracingASGIMiddleware:
    """
    ASGI middleware that wraps HTTP requests with OpenTelemetry spans.
    Ensures every HTTP request gets a root span named after its method.
    """

    def __init__(self, app):
        self.app = app
        from opentelemetry import trace as _trace
        self._trace = _trace
        self.tracer = _trace.get_tracer("asgi")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        query = scope.get("query_string", b"").decode("utf-8", "replace")
        target = f"{path}?{query}" if query else path

        with self.tracer.start_as_current_span(
            f"{method} {target}",
            kind=self._trace.SpanKind.SERVER,
        ) as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.target", target)
            span.set_attribute("http.scheme", scope.get("scheme", "http"))
            span.set_attribute("http.host", dict(scope.get("headers", [])).get(b"host", b"").decode("latin1"))
            span.set_attribute("http.flavor", scope.get("http_version", "1.1"))

            async def wrapped_send(message):
                if message.get("type") == "http.response.start":
                    status = message.get("status", 0)
                    span.set_attribute("http.status_code", status)
                    if status >= 500:
                        span.set_status(self._trace.Status(self._trace.StatusCode.ERROR))
                await send(message)

            try:
                await self.app(scope, receive, wrapped_send)
            except Exception as exc:
                span.set_status(self._trace.Status(self._trace.StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise


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
    "http": TracingASGIMiddleware(MetricsHostASGIMiddleware(django_asgi_app)),
    # The b2b workspace's own routes come first: both lists are ordered and
    # `chat.routing` matches a bare `ws/chat/`, so anything the workspace adds
    # under its own prefix must be reachable regardless of what the older
    # consumer claims.
    "websocket": OptionalOriginAllowedHostsValidator(
        URLRouter(workspace_websocket_urlpatterns + websocket_urlpatterns)
    ),
})
      
