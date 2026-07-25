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
