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

Host header: Prometheus scrapes the container by its Docker/Dokploy service name
(`weel-devbackend-xyz:8000`), which is never in DJANGO_ALLOWED_HOSTS, so
CommonMiddleware answered every scrape with 400 DisallowedHost and the
BackendDown alert fired against a healthy backend (2026-09-05). For an
*authorized* scrape of a guarded path we therefore substitute the first
configured allowed host when the incoming one would be rejected — the response
is a metrics dump that never uses the host, and the caller already proved it is
internal or holds the token.
"""
from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpResponseNotFound
from django.http.request import split_domain_port, validate_host

_GUARDED_PREFIXES = ("/metrics",)


def _substitute_host() -> str:
    """A host that passes ALLOWED_HOSTS: the first concrete (non-wildcard) entry."""
    for host in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if host and host != "*" and not host.startswith("."):
            return host
    for host in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if host.startswith(".") and len(host) > 1:
            return "metrics" + host
    return "localhost"


def _host_allowed(request) -> bool:
    raw = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME") or ""
    domain, _port = split_domain_port(raw)
    return bool(domain) and validate_host(domain, settings.ALLOWED_HOSTS)


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
            if not _host_allowed(request):
                request.META["HTTP_HOST"] = _substitute_host()
                request.META.pop("HTTP_X_FORWARDED_HOST", None)
        return self.get_response(request)
