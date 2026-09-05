from __future__ import annotations

import json
from time import time
from unittest.mock import patch
from uuid import uuid4

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.middleware.cache import CacheMiddleware


class CacheMiddlewareTests(SimpleTestCase):
    @patch("core.middleware.cache.cache.get")
    def test_cached_uuid_payload_serializes_on_cache_hit(self, mock_cache_get):
        token = "token-123"
        service_guid = uuid4()
        mock_cache_get.side_effect = [
            1,
            {
                "data": {
                    "results": [
                        {
                            "guid": str(uuid4()),
                            "services": [service_guid],
                        }
                    ]
                },
                "status": 200,
                "content_type": "application/json",
                "cached_at": time(),
            },
        ]

        middleware = CacheMiddleware(
            lambda _request: HttpResponse(
                "should not run",
                status=599,
                content_type="text/plain",
            )
        )
        request = RequestFactory().get(
            "/api/property/recommendations/?type=most-booked",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertEqual(
            payload["results"][0]["services"],
            [str(service_guid)],
        )


class MetricsGuardMiddlewareTests(SimpleTestCase):
    """core.middleware.metrics_guard — /metrics is internal-only once a token is set."""

    def _get(self, path="/metrics", token="secret-token", **meta):
        from django.test import override_settings

        from core.middleware.metrics_guard import MetricsGuardMiddleware

        with override_settings(PROMETHEUS_METRICS_TOKEN=token):
            middleware = MetricsGuardMiddleware(lambda request: HttpResponse("ok"))
        request = RequestFactory().get(path, **meta)
        return middleware(request)

    def test_open_when_no_token_configured(self):
        response = self._get(token="", REMOTE_ADDR="8.8.8.8")
        self.assertEqual(response.status_code, 200)

    def test_public_caller_gets_404(self):
        response = self._get(REMOTE_ADDR="8.8.8.8")
        self.assertEqual(response.status_code, 404)

    def test_private_scraper_allowed(self):
        response = self._get(REMOTE_ADDR="172.18.0.7")
        self.assertEqual(response.status_code, 200)

    def test_bearer_token_allowed_from_anywhere(self):
        response = self._get(
            REMOTE_ADDR="8.8.8.8", HTTP_AUTHORIZATION="Bearer secret-token"
        )
        self.assertEqual(response.status_code, 200)

    def test_wrong_bearer_token_rejected(self):
        response = self._get(
            REMOTE_ADDR="8.8.8.8", HTTP_AUTHORIZATION="Bearer nope"
        )
        self.assertEqual(response.status_code, 404)

    def test_spoofed_forwarded_for_does_not_bypass(self):
        # Traefik appends the real client last; a client-supplied private IP
        # at the front of the list must not count.
        response = self._get(
            REMOTE_ADDR="172.18.0.2",
            HTTP_X_FORWARDED_FOR="10.0.0.1, 8.8.8.8",
        )
        self.assertEqual(response.status_code, 404)

    def test_other_paths_untouched(self):
        response = self._get(path="/health/", REMOTE_ADDR="8.8.8.8")
        self.assertEqual(response.status_code, 200)

    def _get_host(self, path="/metrics", token="secret-token", allowed=("dev.weel.uz",), **meta):
        """Like _get, but returns the HTTP_HOST the downstream app ends up seeing."""
        from django.test import override_settings

        from core.middleware.metrics_guard import MetricsGuardMiddleware

        seen = {}

        def downstream(request):
            seen["host"] = request.META.get("HTTP_HOST")
            return HttpResponse("ok")

        with override_settings(PROMETHEUS_METRICS_TOKEN=token, ALLOWED_HOSTS=list(allowed)):
            middleware = MetricsGuardMiddleware(downstream)
            request = RequestFactory().get(path, **meta)
            response = middleware(request)
        return response, seen.get("host")

    def test_internal_scrape_by_service_name_gets_an_allowed_host(self):
        # Prometheus scrapes weel-devbackend-xyz:8000 — never in ALLOWED_HOSTS.
        response, host = self._get_host(
            REMOTE_ADDR="10.0.1.5", HTTP_HOST="weel-devbackend-y95c8w:8000"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(host, "dev.weel.uz")

    def test_wildcard_allowed_hosts_get_a_concrete_substitute(self):
        response, host = self._get_host(
            allowed=(".weel.uz",), REMOTE_ADDR="10.0.1.5", HTTP_HOST="weel-devbackend-y95c8w:8000"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(host, "metrics.weel.uz")

    def test_internal_scrape_host_rewritten_even_without_token(self):
        response, host = self._get_host(
            token="", REMOTE_ADDR="10.0.1.5", HTTP_HOST="weel-devbackend-y95c8w:8000"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(host, "dev.weel.uz")

    def test_public_scrape_without_token_is_not_rewritten(self):
        response, host = self._get_host(token="", REMOTE_ADDR="8.8.8.8", HTTP_HOST="evil.example")
        self.assertEqual(response.status_code, 200)  # guard is off without a token
        self.assertEqual(host, "evil.example")  # but no host laundering for outsiders

    def test_full_stack_scrape_by_service_name_returns_metrics(self):
        from django.test import Client, override_settings

        with override_settings(ALLOWED_HOSTS=["dev.weel.uz"], PROMETHEUS_METRICS_TOKEN="", DEBUG=False):
            client = Client()
            client.handler.load_middleware()
            response = client.get("/metrics", HTTP_HOST="weel-devbackend-y95c8w:8000", REMOTE_ADDR="10.0.1.12")
        self.assertNotEqual(response.status_code, 400)

    def test_allowed_host_is_left_alone(self):
        response, host = self._get_host(REMOTE_ADDR="10.0.1.5", HTTP_HOST="dev.weel.uz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(host, "dev.weel.uz")

    def test_unauthorized_scrape_host_is_not_rewritten(self):
        response, host = self._get_host(REMOTE_ADDR="8.8.8.8", HTTP_HOST="weel-devbackend-y95c8w:8000")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(host)

    def test_non_metrics_path_host_untouched(self):
        response, host = self._get_host(path="/api/", REMOTE_ADDR="10.0.1.5", HTTP_HOST="evil.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(host, "evil.example")


class MetricsHostASGIMiddlewareTests(SimpleTestCase):
    """core.asgi.MetricsHostASGIMiddleware fixes the Host header before Django sees it."""

    def _run(self, path="/metrics", client="10.0.1.12", host=b"weel-devbackend-y95c8w:8000", auth=None, allowed=("dev.weel.uz",), token=""):
        import asyncio

        from django.test import override_settings

        from core.asgi import MetricsHostASGIMiddleware

        seen = {}

        async def app(scope, receive, send):
            seen["headers"] = dict(scope["headers"])

        headers = [(b"host", host), (b"user-agent", b"Prometheus/3.5")]
        if auth:
            headers.append((b"authorization", auth))
        scope = {"type": "http", "path": path, "headers": headers, "client": (client, 51000)}
        with override_settings(ALLOWED_HOSTS=list(allowed), PROMETHEUS_METRICS_TOKEN=token):
            asyncio.run(MetricsHostASGIMiddleware(app)(scope, None, None))
        return seen["headers"]

    def test_internal_scrape_host_is_replaced(self):
        self.assertEqual(self._run()[b"host"], b"dev.weel.uz")

    def test_public_client_host_untouched(self):
        self.assertEqual(self._run(client="8.8.8.8")[b"host"], b"weel-devbackend-y95c8w:8000")

    def test_token_from_outside_is_enough(self):
        headers = self._run(client="8.8.8.8", auth=b"Bearer t0k", token="t0k")
        self.assertEqual(headers[b"host"], b"dev.weel.uz")

    def test_other_paths_untouched(self):
        self.assertEqual(self._run(path="/api/")[b"host"], b"weel-devbackend-y95c8w:8000")

    def test_allowed_host_untouched(self):
        self.assertEqual(self._run(host=b"dev.weel.uz")[b"host"], b"dev.weel.uz")


class ChannelsRedisUrlTests(SimpleTestCase):
    """core.settings strips socket timeouts from the channel-layer Redis URL."""

    def test_socket_timeout_params_are_dropped(self):
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        url = "redis://default:pw@weel-redis:6379/0?socket_timeout=3&socket_connect_timeout=3&ssl_cert_reqs=none"
        parts = urlsplit(url)
        query = urlencode(
            [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k not in {"socket_timeout", "socket_connect_timeout", "timeout"}
            ]
        )
        self.assertEqual(
            urlunsplit(parts._replace(query=query)),
            "redis://default:pw@weel-redis:6379/0?ssl_cert_reqs=none",
        )
