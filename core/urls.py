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

from django.urls import path, include, re_path
from django.conf.urls.static import static
from django.views.static import serve
from django.http import JsonResponse

from rest_framework import permissions

from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from core import settings

schema_view = get_schema_view(
    openapi.Info(
        "Weel API",
        "v1",
        "API documentation for the Weel backend",
        contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
        license=openapi.License(name="Proprietary"),
    ),
    public=False,
    url=None,
    permission_classes=[permissions.AllowAny],
)

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
            schema_view.with_ui("swagger", cache_timeout=0),
            name="schema-swagger-ui",
        )
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
