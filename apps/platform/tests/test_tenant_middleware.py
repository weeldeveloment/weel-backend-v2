"""Tenant routing: which PostgreSQL schema a request is allowed to read.

This middleware runs before authentication and picks the schema from the
bearer token, so it is the boundary between tenants. Two properties matter:

1. It trusts only a *signature-verified* token. Reading the payload without
   verifying it let anyone mint `organization_id` and read another tenant.
2. It always puts `search_path` back. `search_path` is connection state, not
   request state — leaving a tenant's schema on the connection means the next
   request served by it reads the previous caller's data.
"""
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from core.middleware.tenant import TenantMiddleware

factory = RequestFactory()

ORG = {
    "id": 3,
    "name": "Mehmonxona Uz",
    "slug": "mehmonxona-uz",
    "schema_name": "tenant_abc123",
    "is_active": True,
}


def _token(user_type="pms", organization_id=3):
    token = AccessToken()
    token["user_type"] = user_type
    token["sub"] = "7"
    if organization_id is not None:
        token["organization_id"] = organization_id
    return str(token)


def _request(authorization=None):
    headers = {"HTTP_AUTHORIZATION": authorization} if authorization else {}
    return factory.get("/pms/properties/", **headers)


class _Executed:
    """Records the SQL the middleware runs, standing in for a cursor."""

    def __init__(self):
        self.statements = []

    def __call__(self, sql, params=None):
        self.statements.append((sql, params))

    def schema_switches(self):
        return [p[0] for sql, p in self.statements if "SET search_path" in sql and p]


def _run(request, view, org=ORG, schema_exists=True):
    """Drive the middleware with the database calls stubbed out."""
    executed = _Executed()

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            executed(sql, params)

        def fetchone(self):
            return (schema_exists,)

    with patch("core.middleware.tenant.connection") as conn, \
         patch("core.middleware.tenant.get_cached_organization", return_value=org), \
         patch("core.middleware.tenant.invalidate_org_schema_cache"):
        conn.cursor.return_value = FakeCursor()
        middleware = TenantMiddleware(view)
        try:
            response = middleware(request)
        except Exception as exc:  # surfaced to the caller, after the finally ran
            return executed, exc
    return executed, response


def _ok_view(request):
    return "response"


# ─── Signature verification ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_valid_pms_token_switches_to_the_tenant_schema():
    executed, response = _run(_request(f"Bearer {_token()}"), _ok_view)

    assert response == "response"
    assert "tenant_abc123" in executed.schema_switches()


@pytest.mark.django_db
def test_a_forged_token_is_ignored():
    """The payload says organization 3, but nothing signed it."""
    import base64
    import json

    payload = base64.urlsafe_b64encode(
        json.dumps({"user_type": "pms", "organization_id": 3, "sub": "7"}).encode()
    ).decode().rstrip("=")
    forged = f"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.{payload}.not-a-real-signature"

    executed, response = _run(_request(f"Bearer {forged}"), _ok_view)

    assert response == "response"
    assert executed.schema_switches() == []


@pytest.mark.django_db
def test_a_token_from_another_audience_is_ignored():
    executed, _ = _run(_request(f"Bearer {_token(user_type='client')}"), _ok_view)
    assert executed.schema_switches() == []


@pytest.mark.django_db
def test_a_request_with_no_token_stays_on_public():
    executed, _ = _run(_request(), _ok_view)
    assert executed.schema_switches() == []


@pytest.mark.django_db
def test_a_token_without_an_organization_stays_on_public():
    """Freshly registered accounts carry no organization_id."""
    executed, _ = _run(_request(f"Bearer {_token(organization_id=None)}"), _ok_view)
    assert executed.schema_switches() == []


@pytest.mark.django_db
def test_an_unknown_organization_does_not_switch_schemas():
    executed, _ = _run(_request(f"Bearer {_token()}"), _ok_view, org=None)
    assert executed.schema_switches() == []


@pytest.mark.django_db
def test_a_missing_schema_does_not_switch_schemas():
    executed, _ = _run(_request(f"Bearer {_token()}"), _ok_view, schema_exists=False)
    assert executed.schema_switches() == []


# ─── Resetting search_path ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_search_path_is_reset_after_a_normal_response():
    executed, _ = _run(_request(f"Bearer {_token()}"), _ok_view)

    assert executed.schema_switches()[-1] == "public"


@pytest.mark.django_db
def test_search_path_is_reset_even_when_the_view_raises():
    """The reason the reset lives in a `finally`.

    A view that blows up must not leave the connection pointing at that
    tenant's schema, or the next request to reuse the connection reads it.
    """

    def exploding_view(request):
        raise RuntimeError("view blew up")

    executed, outcome = _run(_request(f"Bearer {_token()}"), exploding_view)

    assert isinstance(outcome, RuntimeError)
    assert executed.schema_switches()[-1] == "public"
