from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver

from rest_framework.test import APIClient, APIRequestFactory

from admin_auth.authentication import create_admin_tokens
from admin_auth.raw_repository import create_admin_user
from users.raw_repository import create_client, create_partner
from users.tokens import create_client_tokens, create_partner_tokens


DUMMY_UUID = "00000000-0000-0000-0000-000000000001"
_ROUTE_PARAM_RE = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")
_REGEX_PARAM_RE = re.compile(r"\(\?P(?:<(?P<name>[^>]+)>)?[^)]+\)")
_SUPPORTED_STATUSES = {200, 201, 204, 301, 302, 400, 401, 403, 404, 405, 429, 501}


@dataclass(frozen=True)
class EndpointCase:
    label: str
    method: str
    path: str
    auth: str | None = None
    data: dict | None = None
    format: str = "json"
    expected_statuses: tuple[int, ...] = (200, 201, 204, 400, 401, 403, 404, 405, 429, 501)


def _sample_value(name: str, converter: str | None = None) -> str:
    if name == "secret_token":
        return "invalid-smoke-secret"
    if name in {"partner_id", "pk"}:
        return "1"
    if converter == "uuid" or name.endswith("_id") or name in {"story_id", "media_id", "booking_id", "property_id"}:
        return DUMMY_UUID
    return "smoke"


def _normalize_route(raw_route: str) -> str | None:
    route = raw_route.strip()
    if not route:
        return None

    route = route.replace("^", "").replace("$", "").replace("\\Z", "")
    if "(?P<format>" in route or "<drf_format_suffix:format>" in route:
        return None

    route = route.replace("\\.", ".")
    route = _ROUTE_PARAM_RE.sub(
        lambda match: _sample_value(match.group("name"), match.group("converter")),
        route,
    )
    route = _REGEX_PARAM_RE.sub(
        lambda match: _sample_value(match.group("name") or "param"),
        route,
    )
    route = route.replace("//", "/")
    route = route.lstrip("/")
    if not route.startswith("api/"):
        return None
    if route.endswith("/") is False:
        route = f"{route}/"
    if "admin-auth/token/refresh/" in route:
        return None
    return f"/{route}"


def _preferred_method(pattern: URLPattern) -> str | None:
    actions = getattr(pattern.callback, "actions", None)
    if actions:
        for method in ("get", "post", "patch", "put", "delete"):
            if method in actions:
                return method

    view_cls = getattr(pattern.callback, "cls", None)
    if view_cls is not None:
        for method in ("get", "post", "patch", "put", "delete"):
            if hasattr(view_cls, method):
                return method

    if "webhook" in str(pattern.pattern):
        return "post"
    return None


def _collect_api_patterns(patterns, prefix: str = "") -> list[tuple[str, URLPattern]]:
    collected: list[tuple[str, URLPattern]] = []
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            nested_prefix = prefix + (
                getattr(pattern.pattern, "_route", None) or pattern.pattern.regex.pattern
            )
            collected.extend(_collect_api_patterns(pattern.url_patterns, nested_prefix))
            continue

        route = prefix + (
            getattr(pattern.pattern, "_route", None) or pattern.pattern.regex.pattern
        )
        collected.append((route, pattern))
    return collected


class AllAPIEndpointsSmokeTests(TestCase):
    def _request(self, client: APIClient, method: str, path: str, data: dict | None = None, *, format: str = "json"):
        fn = getattr(client, method.lower())
        if method.lower() in {"get", "head"}:
            return fn(path, data=data or {})
        return fn(path, data=data or {}, format=format)

    def _payload_for(self, path: str, method: str) -> tuple[dict | None, str]:
        if method == "post" and path == "/api/logs/frontend/":
            return {
                "level": "info",
                "message": "smoke-test",
                "extra": {"suite": "api-smoke"},
            }, "json"
        if method == "post" and path == "/api/bot/webhook/invalid-smoke-secret/":
            return {"update_id": 1}, "json"
        if method == "post" and path.endswith("/refresh/"):
            return {"refresh": "invalid-refresh-token"}, "json"
        if method == "post" and path.endswith("/chat/read/"):
            return {"message_ids": []}, "json"
        return {}, "json"

    def test_every_api_endpoint_returns_non_500_for_a_safe_request(self):
        client = APIClient()
        seen: set[tuple[str, str]] = set()

        for raw_route, pattern in _collect_api_patterns(get_resolver().url_patterns):
            path = _normalize_route(raw_route)
            if path is None:
                continue

            view_name = getattr(getattr(pattern.callback, "cls", None), "__name__", "")
            if view_name == "APIRootView":
                continue

            method = _preferred_method(pattern)
            if method is None:
                continue

            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            payload, request_format = self._payload_for(path, method)
            with self.subTest(method=method.upper(), path=path, view=view_name or "function-view"):
                response = self._request(client, method, path, payload, format=request_format)
                self.assertIn(
                    response.status_code,
                    _SUPPORTED_STATUSES,
                    msg=f"{method.upper()} {path} returned unexpected status {response.status_code}",
                )


class AuthenticatedAPIEndpointsSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        request = APIRequestFactory().get("/")

        cls.client_user = create_client(
            phone_number=f"99890{uuid.uuid4().int % 10**7:07d}",
            first_name="Smoke",
            last_name="Client",
        )
        cls.partner_user = create_partner(
            phone_number=f"99891{uuid.uuid4().int % 10**7:07d}",
            username=f"smoke_partner_{uuid.uuid4().hex[:8]}",
            first_name="Smoke",
            last_name="Partner",
            email=f"smoke_{uuid.uuid4().hex[:8]}@example.com",
        )
        cls.admin_user = create_admin_user(
            email=f"admin_{uuid.uuid4().hex[:8]}@example.com",
            username=f"smoke_admin_{uuid.uuid4().hex[:8]}",
            first_name="Smoke",
            last_name="Admin",
        )

        cls.client_tokens = create_client_tokens(cls.client_user, request)
        cls.partner_tokens = create_partner_tokens(cls.partner_user, request)
        cls.admin_tokens = create_admin_tokens(cls.admin_user)

    def _api_client_for(self, auth: str | None) -> APIClient:
        client = APIClient()
        if auth == "client":
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.client_tokens['access']}")
        elif auth == "partner":
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.partner_tokens['access']}")
        elif auth == "admin":
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_tokens['access']}")
        return client

    def _request(self, case: EndpointCase):
        client = self._api_client_for(case.auth)
        fn = getattr(client, case.method.lower())
        if case.method.lower() in {"get", "head"}:
            return fn(case.path, data=case.data or {})
        return fn(case.path, data=case.data or {}, format=case.format)

    def test_authenticated_smoke_paths_do_not_500(self):
        cases = [
            EndpointCase("client profile", "get", "/api/user/client/profile/", "client", expected_statuses=(200,)),
            EndpointCase("client profile update", "patch", "/api/user/client/profile/update/", "client", data={"first_name": "Smoke2"}, expected_statuses=(200,)),
            EndpointCase("client favorites", "get", "/api/property/properties/favorites/", "client", expected_statuses=(200,)),
            EndpointCase("client favorite toggle missing property", "post", f"/api/property/properties/{DUMMY_UUID}/favorite/", "client", expected_statuses=(404,)),
            EndpointCase("client booking history", "get", "/api/booking/client/history/", "client", expected_statuses=(200,)),
            EndpointCase("client booking detail missing", "get", f"/api/booking/client/{DUMMY_UUID}/", "client", expected_statuses=(404,)),
            EndpointCase("client booking cancel missing", "post", f"/api/booking/client/{DUMMY_UUID}/cancel/", "client", expected_statuses=(404,)),
            EndpointCase("client fcm update", "post", "/api/notification/device/", "client", data={"fcm_token": "smoke-client-token", "device_type": "ios"}, expected_statuses=(200,)),
            EndpointCase("partner profile", "get", "/api/user/partner/profile/", "partner", expected_statuses=(200,)),
            EndpointCase("partner profile update", "patch", "/api/user/partner/profile/update/", "partner", data={"first_name": "Smoke2"}, expected_statuses=(200,)),
            EndpointCase("partner passport upload missing file", "post", "/api/user/partner/documents/passport/", "partner", data={}, format="multipart", expected_statuses=(400,)),
            EndpointCase("partner property list", "get", "/api/property/partner/properties/", "partner", expected_statuses=(200,)),
            EndpointCase("partner all properties", "get", "/api/property/partner/all/", "partner", expected_statuses=(200,)),
            EndpointCase("admin partner all missing partner_id", "get", "/api/property/partner/all/", "admin", expected_statuses=(400,)),
            EndpointCase(
                "admin partner all with partner_id",
                "get",
                f"/api/property/partner/all/?partner_id={self.partner_user.id}",
                "admin",
                expected_statuses=(200,),
            ),
            EndpointCase("partner story list", "get", "/api/story/partner/stories/", "partner", expected_statuses=(200,)),
            EndpointCase("partner story create invalid", "post", "/api/story/stories/", "partner", data={}, format="multipart", expected_statuses=(400,)),
            EndpointCase("partner calendar block missing property", "post", f"/api/booking/properties/{DUMMY_UUID}/calendar/block/", "partner", data={"from_date": "2026-04-10", "to_date": "2026-04-11"}, expected_statuses=(404,)),
            EndpointCase("partner booking list", "get", "/api/booking/partner/", "partner", expected_statuses=(200,)),
            EndpointCase("partner booking accept missing", "post", f"/api/booking/partner/{DUMMY_UUID}/accept/", "partner", expected_statuses=(400, 404)),
            EndpointCase("partner notifications", "get", "/api/notification/partner/", "partner", expected_statuses=(200,)),
            EndpointCase("partner notifications read", "post", "/api/notification/partner/read/", "partner", data={"notification_ids": []}, expected_statuses=(200,)),
            EndpointCase("partner notifications read all", "post", "/api/notification/partner/read-all/", "partner", data={}, expected_statuses=(200,)),
            EndpointCase("partner fcm update", "post", "/api/notification/partner/device/", "partner", data={"fcm_token": "smoke-partner-token", "device_type": "android"}, expected_statuses=(200,)),
            EndpointCase("admin me", "get", "/api/admin-auth/me/", "admin", expected_statuses=(200,)),
            EndpointCase("admin clients list", "get", "/api/admin-auth/users/clients/", "admin", expected_statuses=(200,)),
            EndpointCase("admin partners list", "get", "/api/admin-auth/users/partners/", "admin", expected_statuses=(200,)),
            EndpointCase("admin register invalid payload", "post", "/api/admin-auth/register/", "admin", data={}, expected_statuses=(400, 403)),
            EndpointCase("admin booking list", "get", "/api/booking/admin/bookings/", "admin", expected_statuses=(200,)),
            EndpointCase("admin all properties list", "get", "/api/property/admin/properties/all/", "admin", expected_statuses=(200,)),
            EndpointCase("admin chat conversations", "get", "/api/chat/conversations/", "admin", expected_statuses=(200,)),
            EndpointCase("admin chat messages missing partner", "get", "/api/chat/messages/1/", "admin", expected_statuses=(404,)),
            EndpointCase("admin chat send invalid", "post", "/api/chat/send/", "admin", data={}, expected_statuses=(400,)),
            EndpointCase("admin chat read", "post", "/api/chat/read/", "admin", data={"message_ids": []}, expected_statuses=(200,)),
        ]

        for case in cases:
            with self.subTest(case.label):
                response = self._request(case)
                self.assertIn(
                    response.status_code,
                    case.expected_statuses,
                    msg=f"{case.method.upper()} {case.path} returned {response.status_code}",
                )
