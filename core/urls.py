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
import functools
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
from core.swagger_hooks import RequiredFixOpenAPISchemaGenerator

schema_info = openapi.Info(
    "Weel API",
    "v1",
    "API documentation for the Weel backend",
    contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
    license=openapi.License(name="Proprietary"),
)

schema_view = get_schema_view(
    schema_info,
    # public=True shows endpoints even when they require authentication;
    # otherwise drf_yasg hides them from the schema for anonymous users.
    public=True,
    url=settings.SWAGGER_URL,
    permission_classes=[permissions.AllowAny],
    generator_class=RequiredFixOpenAPISchemaGenerator,
)

# Separate Swagger schema — only B2B/Hotels/Documents + admin hotel & b2b endpoints
def _build_b2b_patterns():
    from apps.admin_auth.hotel_views import (
        AdminHotelListView, AdminHotelDetailView, AdminHotelClassifyView,
        AdminHotelRoomInventoryView, AdminHotelCalendarView, AdminHotelBookingsView,
        AdminHotelReviewsView, AdminReviewRespondView, AdminReviewHideView,
        AdminB2BCompaniesView, AdminB2BCompanyDetailView, AdminB2BUsersView,
        AdminB2BSupportThreadsView, AdminB2BSupportThreadView,
    )
    return [
        path("api/b2b/", include("apps.b2b.urls")),
        path("api/documents/", include("apps.documents.urls")),
        path("api/hotels/", include("apps.hotels.urls")),
        # Only hotel-management and B2B-company admin endpoints (no login/register/users)
        path("api/admin-auth/hotels/", AdminHotelListView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/", AdminHotelDetailView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/classify/", AdminHotelClassifyView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/rooms/", AdminHotelRoomInventoryView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/calendar/", AdminHotelCalendarView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/bookings/", AdminHotelBookingsView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/reviews/", AdminHotelReviewsView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/reviews/<int:review_id>/respond/", AdminReviewRespondView.as_view()),
        path("api/admin-auth/hotels/<int:property_id>/reviews/<int:review_id>/hide/", AdminReviewHideView.as_view()),
        path("api/admin-auth/b2b/companies/", AdminB2BCompaniesView.as_view()),
        path("api/admin-auth/b2b/companies/<int:company_id>/", AdminB2BCompanyDetailView.as_view()),
        path("api/admin-auth/b2b/companies/<int:company_id>/users/", AdminB2BUsersView.as_view()),
        path("api/admin-auth/b2b/support/", AdminB2BSupportThreadsView.as_view()),
        path("api/admin-auth/b2b/support/<int:employee_id>/", AdminB2BSupportThreadView.as_view()),
    ]

_b2b_patterns = _build_b2b_patterns()

b2b_schema_view = get_schema_view(
    openapi.Info(
        "Weel B2B API",
        "v1",
        "B2B Corporate Travel Management —Business Trips, Documents, Hotel Catalog, Admin",
        contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
        license=openapi.License(name="Proprietary"),
    ),
    public=True,
    url=settings.SWAGGER_URL,
    permission_classes=[permissions.AllowAny],
    patterns=_b2b_patterns,
    generator_class=RequiredFixOpenAPISchemaGenerator,
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


def _docs_with_optional_basic_auth(request, *args, _docs_view=None, **kwargs):
    """Gate an API-docs view behind the optional Swagger basic auth.

    Takes the view to render as a keyword argument so every docs route —
    Swagger, Redoc, and the B2B-only variants — goes through the same check.
    Previously only /swagger/ was wrapped, so /redoc/ and /b2b/swagger/ served
    the full API schema to anyone even when the password was configured.
    """
    if _docs_view is None:
        _docs_view = schema_view.with_ui("swagger", cache_timeout=0)

    if not _swagger_auth_required():
        return _docs_view(request, *args, **kwargs)

    if _swagger_cookie_is_valid(request):
        return _docs_view(request, *args, **kwargs)

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
    response = _docs_view(request, *args, **kwargs)
    _set_swagger_cookie(request, response)
    return response


def _protected_docs(docs_view):
    """Bind `docs_view` into the basic-auth gate so it can be used as a route."""
    return functools.partial(_docs_with_optional_basic_auth, _docs_view=docs_view)


urlpatterns = [
    path("health/", lambda request: JsonResponse({"status": "ok"})),
]

if settings.PROMETHEUS_ENABLED:
    urlpatterns += [path("", include("django_prometheus.urls"))]

urlpatterns += [
    path("i18n/", include("django.conf.urls.i18n")),
    path("b2b/", include("apps.b2b.urls")),
    path("documents/", include("apps.documents.urls")),
    path("hotels/", include("apps.hotels.urls")),
    path("activities/", include("apps.activities.urls")),
    path("api/", include("apps.urls")),
    path("api/platform/", include("apps.platform.urls")),
    path("api/pms/", include("apps.pms.urls")),
    path("api/pms/", include("apps.bookingcom.urls")),
    path("api/b2b/", include("apps.b2b.urls")),
    path("api/documents/", include("apps.documents.urls")),
    path("api/hotels/", include("apps.hotels.urls")),
    path("api/activities/", include("apps.activities.urls")),
]

if settings.ENABLE_SWAGGER_UI:
    # Every docs route goes through _protected_docs so the basic-auth password
    # actually covers the schema, not just the Swagger UI page.
    _swagger_ui = _protected_docs(schema_view.with_ui("swagger", cache_timeout=0))
    _redoc_ui = _protected_docs(schema_view.with_ui("redoc", cache_timeout=0))
    _b2b_swagger_ui = _protected_docs(b2b_schema_view.with_ui("swagger", cache_timeout=0))
    _b2b_redoc_ui = _protected_docs(b2b_schema_view.with_ui("redoc", cache_timeout=0))

    urlpatterns += [
        path("swagger/", _swagger_ui, name="schema-swagger-ui"),
        path("api/swagger/", _swagger_ui, name="schema-swagger-ui-api-prefix"),
        path("redoc/", _redoc_ui, name="schema-redoc-ui"),
        path("api/redoc/", _redoc_ui, name="schema-redoc-ui-api-prefix"),
        # B2B-only Swagger — shows only B2B, Documents, Hotels, Admin B2B endpoints
        path("b2b/swagger/", _b2b_swagger_ui, name="b2b-schema-swagger-ui"),
        re_path(r"^b2b/swagger$", _b2b_swagger_ui),
        path("b2b/redoc/", _b2b_redoc_ui, name="b2b-schema-redoc-ui"),
        re_path(r"^b2b/redoc$", _b2b_redoc_ui),
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
