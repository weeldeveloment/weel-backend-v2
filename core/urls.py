"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

import base64
import hmac
import time

from django.urls import path, include, re_path
from django.conf.urls.static import static
from django.views.static import serve
from django.http import JsonResponse, HttpResponse, HttpResponseRedirect
from django.core.cache import cache
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired

from rest_framework import permissions

from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from core import settings


_SWAGGER_INFO = openapi.Info(
    "Weel API",
    "v1",
    "API documentation for the Weel backend",
    contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
    license=openapi.License(name="Proprietary"),
)


def _is_local_request(request) -> bool:
    host = (request.get_host() or "").split(":", 1)[0].strip().lower()
    return host in {"127.0.0.1", "localhost"}


def _build_schema_view(request=None):
    # Force same-origin on localhost so Swagger "Try it out" doesn't call remote
    # domains and fail with browser NetworkError/CORS issues.
    swagger_url = None if (request is not None and _is_local_request(request)) else settings.SWAGGER_URL
    return get_schema_view(
        _SWAGGER_INFO,
        # public=True shows endpoints even when they require authentication;
        # otherwise drf_yasg hides them from the schema for anonymous users.
        public=True,
        url=swagger_url,
        permission_classes=[permissions.AllowAny],
    )

_SWAGGER_LOCAL_AUTH_ATTEMPTS: dict[str, tuple[int, float]] = {}
_SWAGGER_AUTH_COOKIE = "swagger_auth"
_SWAGGER_AUTH_SALT = "swagger-basic-auth-cookie"


def _swagger_auth_required() -> bool:
    return bool(
        settings.SWAGGER_BASIC_AUTH_USERNAME and settings.SWAGGER_BASIC_AUTH_PASSWORD
    )


def _swagger_unauthorized_response():
    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <title>Swagger Login Required</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .card { max-width: 520px; border: 1px solid #ddd; border-radius: 8px; padding: 20px; }
        button { background: #0b5ed7; color: #fff; border: none; border-radius: 6px; padding: 10px 14px; cursor: pointer; }
        button:hover { background: #084db2; }
      </style>
    </head>
    <body>
      <div class="card">
        <h3>401 Unauthorized</h3>
        <p>Swagger access requires authentication.</p>
        <form method="get" action="">
          <div style="margin-bottom: 10px;">
            <input type="text" name="swagger_username" placeholder="Username" required />
          </div>
          <div style="margin-bottom: 10px;">
            <input type="password" name="swagger_password" placeholder="Password" required />
          </div>
          <button type="submit">Sign in</button>
        </form>
      </div>
    </body>
    </html>
    """
    return HttpResponse(html, status=401, content_type="text/html; charset=utf-8")


def _swagger_cookie_is_valid(request) -> bool:
    token = request.COOKIES.get(_SWAGGER_AUTH_COOKIE)
    if not token:
        return False
    try:
        payload = signing.loads(
            token,
            salt=_SWAGGER_AUTH_SALT,
            max_age=settings.SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return False
    username = payload.get("u")
    return bool(
        username
        and hmac.compare_digest(username, settings.SWAGGER_BASIC_AUTH_USERNAME)
    )


def _set_swagger_cookie(request, response):
    token = signing.dumps(
        {"u": settings.SWAGGER_BASIC_AUTH_USERNAME},
        salt=_SWAGGER_AUTH_SALT,
    )
    response.set_cookie(
        _SWAGGER_AUTH_COOKIE,
        token,
        max_age=settings.SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=bool(getattr(settings, "SESSION_COOKIE_SECURE", False) or request.is_secure()),
    )


def _swagger_with_optional_basic_auth(request, *args, **kwargs):
    schema_view = _build_schema_view(request)
    if not _swagger_auth_required():
        return schema_view.with_ui("swagger", cache_timeout=0)(request, *args, **kwargs)

    if _swagger_cookie_is_valid(request):
        return schema_view.with_ui("swagger", cache_timeout=0)(request, *args, **kwargs)

    ident = request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "unknown"
    ident = ident.split(",")[0].strip()
    fail_cache_key = f"swagger_auth_failures:{ident}"
    try:
        failed_attempts = int(cache.get(fail_cache_key, 0) or 0)
    except Exception:
        # Redis/cache might be unavailable locally; keep auth working with in-process fallback.
        attempts, expires_at = _SWAGGER_LOCAL_AUTH_ATTEMPTS.get(ident, (0, 0.0))
        if expires_at and expires_at <= time.time():
            attempts = 0
            _SWAGGER_LOCAL_AUTH_ATTEMPTS.pop(ident, None)
        failed_attempts = attempts

    if failed_attempts >= settings.SWAGGER_BASIC_AUTH_MAX_ATTEMPTS:
        return HttpResponse(
            "Too many failed login attempts. Try again later.",
            status=429,
        )

    form_username = (request.GET.get("swagger_username") or "").strip()
    form_password = request.GET.get("swagger_password") or ""
    if form_username or form_password:
        is_valid_username = hmac.compare_digest(
            form_username, settings.SWAGGER_BASIC_AUTH_USERNAME
        )
        is_valid_password = hmac.compare_digest(
            form_password, settings.SWAGGER_BASIC_AUTH_PASSWORD
        )
        if is_valid_username and is_valid_password:
            try:
                cache.delete(fail_cache_key)
            except Exception:
                _SWAGGER_LOCAL_AUTH_ATTEMPTS.pop(ident, None)
            response = HttpResponseRedirect(request.path)
            _set_swagger_cookie(request, response)
            return response

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Basic "):
        return _swagger_unauthorized_response()

    try:
        encoded = auth_header.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return _swagger_unauthorized_response()

    is_valid_username = hmac.compare_digest(
        username, settings.SWAGGER_BASIC_AUTH_USERNAME
    )
    is_valid_password = hmac.compare_digest(
        password, settings.SWAGGER_BASIC_AUTH_PASSWORD
    )
    if not (is_valid_username and is_valid_password):
        try:
            cache.set(
                fail_cache_key,
                failed_attempts + 1,
                timeout=settings.SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS,
            )
        except Exception:
            _SWAGGER_LOCAL_AUTH_ATTEMPTS[ident] = (
                failed_attempts + 1,
                time.time() + settings.SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS,
            )
        return _swagger_unauthorized_response()

    try:
        cache.delete(fail_cache_key)
    except Exception:
        _SWAGGER_LOCAL_AUTH_ATTEMPTS.pop(ident, None)
    response = schema_view.with_ui("swagger", cache_timeout=0)(request, *args, **kwargs)
    _set_swagger_cookie(request, response)
    return response


def _redoc_view(request, *args, **kwargs):
    schema_view = _build_schema_view(request)
    return schema_view.with_ui("redoc", cache_timeout=0)(request, *args, **kwargs)


urlpatterns = [
    path("health/", lambda request: JsonResponse({"status": "ok"})),
]

if settings.PROMETHEUS_ENABLED:
    urlpatterns += [path("", include("django_prometheus.urls"))]

urlpatterns += [
    path("i18n/", include("django.conf.urls.i18n")),
    path("api/", include("apps.urls")),
]

if settings.ENABLE_SWAGGER_UI:
    urlpatterns += [
        path(
            "swagger/",
            _swagger_with_optional_basic_auth,
            name="schema-swagger-ui",
        ),
        path(
            "api/swagger/",
            _swagger_with_optional_basic_auth,
            name="schema-swagger-ui-api-prefix",
        ),
        path(
            "redoc/",
            _redoc_view,
            name="schema-redoc-ui",
        ),
        path(
            "api/redoc/",
            _redoc_view,
            name="schema-redoc-ui-api-prefix",
        ),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# `django.conf.urls.static.static(...)` does not create URL patterns when DEBUG=False.
# Serve local media files only when MinIO is not enabled.
if settings.DEBUG and not settings.USE_MINIO:
    urlpatterns += [
        re_path(
            rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        )
    ]
