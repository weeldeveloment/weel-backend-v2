"""
Guard for the django-prometheus /metrics endpoint.

`django_prometheus.urls` mounts /metrics with no auth. In production the scrape
should reach it over the internal Docker network only, with the public Traefik
router blocking /metrics and /health/. This middleware is the in-app backstop for
when that routing is not (yet) in place: unless the request either

  * carries `Authorization: Bearer <PROMETHEUS_METRICS_TOKEN>`, or
  * comes from a private / loopback address,

/metrics responds 404 (404, not 403 — an unauthenticated caller should not even
learn the endpoint exists).

Disabled (endpoint stays open) when PROMETHEUS_METRICS_TOKEN is empty, so local
dev and existing setups are unaffected.
"""
from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpResponseNotFound

_GUARDED_PREFIXES = ("/metrics",)


def _client_ip(request) -> str:
    """
    The address of the peer one hop away. Behind Traefik that is the LAST entry
    of X-Forwarded-For (the one the proxy itself appended); the first entry is
    whatever the client chose to send, so trusting it would let anyone on the
    internet claim `X-Forwarded-For: 10.0.0.1` and read /metrics. Prometheus
    scrapes the container directly, so for it there is no header at all and
    REMOTE_ADDR is the private Docker address.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


class MetricsGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.token = (getattr(settings, "PROMETHEUS_METRICS_TOKEN", "") or "").strip()

    def __call__(self, request):
        if self.token and request.path.startswith(_GUARDED_PREFIXES):
            header = request.META.get("HTTP_AUTHORIZATION", "")
            authorized = header == f"Bearer {self.token}" or _is_private(_client_ip(request))
            if not authorized:
                return HttpResponseNotFound()
        return self.get_response(request)
