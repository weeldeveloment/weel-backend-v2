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

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.views.static import serve

from rest_framework import permissions

from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from core import settings

schema_view = get_schema_view(
    openapi.Info(
        "Property Booking API",
        "v1",
        "API documentation for the property booking service",
        contact=openapi.Contact(name="support@example.com", url="https://example.com"),
        license=openapi.License(name="MIT License"),
    ),
    public=True,
    url=settings.SWAGGER_URL if not settings.DEBUG else "",
    permission_classes=[permissions.AllowAny],
)

try:
    import core.admin_mods
except ImportError:
    pass

urlpatterns = [
    path("", include("django_prometheus.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("api/", include("apps.urls")),
] + i18n_patterns(
    path("admin/", admin.site.urls),
)

urlpatterns += [
    path(
        "swagger/",
        schema_view.with_ui("swagger", cache_timeout=0),
        name="schema-swagger-ui",
    )
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# `django.conf.urls.static.static(...)` does not create URL patterns when DEBUG=False.
# Serve local media files only when MinIO is not enabled.
if not settings.USE_MINIO:
    urlpatterns += [
        re_path(
            rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        )
    ]

