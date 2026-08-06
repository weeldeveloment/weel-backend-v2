from __future__ import annotations

import logging
from typing import Any

from django.utils.deprecation import MiddlewareMixin
from django.db import connection
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import UntypedToken

from apps.platform.raw_repository import get_organization_by_id, _org_schema_cache

logger = logging.getLogger(__name__)


def _get_org_schema(organization_id: int) -> dict[str, Any] | None:
    if organization_id in _org_schema_cache:
        return _org_schema_cache[organization_id]
    result = get_organization_by_id(organization_id)
    _org_schema_cache[organization_id] = result
    return result


def invalidate_org_schema_cache(organization_id: int) -> None:
    _org_schema_cache.pop(organization_id, None)


def _schema_exists(schema_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            [schema_name],
        )
        return cursor.fetchone()[0]


def _extract_org_id_from_jwt(request) -> int | None:
    """Read the tenant's organization id from a *verified* bearer token.

    This runs before authentication and decides which PostgreSQL schema the
    request's queries read from, so the signature has to be checked here.
    Decoding the payload without verifying it let anyone craft a token with an
    arbitrary `organization_id`, point `search_path` at another tenant's
    schema, and then read it through any AllowAny endpoint.
    """
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    raw_token = auth_header.split(" ", 1)[1].strip()
    if not raw_token:
        return None

    try:
        token = UntypedToken(raw_token)  # verifies signature and expiry
    except TokenError:
        return None

    if token.get("user_type") != "pms":
        return None

    org_id = token.get("organization_id")
    if not org_id:
        return None
    try:
        return int(org_id)
    except (TypeError, ValueError):
        return None


class TenantMiddleware(MiddlewareMixin):
    """
    Resolves the tenant schema from the JWT token and sets PostgreSQL search_path.

    Expects the JWT to contain:
      - user_type: "pms"
      - organization_id: int

    Sets search_path to: tenant_<schema_name>, public
    """

    PUBLIC_SCHEMA = "public"

    def process_request(self, request):
        organization_id = _extract_org_id_from_jwt(request)
        if not organization_id:
            return None

        try:
            org = _get_org_schema(int(organization_id))
            if not org or not org.get("schema_name"):
                logger.warning("No schema found for org_id=%s", organization_id)
                return None

            schema_name = org["schema_name"]

            if not _schema_exists(schema_name):
                logger.error("Tenant schema '%s' does not exist for org_id=%s", schema_name, organization_id)
                return None

            request.organization = org
            request.schema_name = schema_name

            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO %s, public", [schema_name])
            request._tenant_search_path_set = True

        except Exception:
            logger.exception("Failed to set tenant schema for org_id=%s", organization_id)
            return None

        return None

    def process_response(self, request, response):
        # Only worth a query when this request actually switched schemas —
        # otherwise every single request paid for a redundant round trip.
        if not getattr(request, "_tenant_search_path_set", False):
            return response
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO %s", [self.PUBLIC_SCHEMA])
        except Exception:
            logger.exception("Failed to reset search_path")
        return response
