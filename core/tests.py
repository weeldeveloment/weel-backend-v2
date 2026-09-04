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
