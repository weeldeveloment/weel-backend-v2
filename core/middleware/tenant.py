from __future__ import annotations

import logging
from typing import Any

from django.utils.deprecation import MiddlewareMixin
from django.db import connection

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
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    try:
        token = auth_header.split(" ", 1)[1].strip()
        parts = token.split(".")
        if len(parts) != 3:
            return None

        import base64
        import json

        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)

        user_type = payload.get("user_type")
        if user_type != "pms":
            return None

        org_id = payload.get("organization_id")
        if org_id:
            return int(org_id)
        return None
    except Exception:
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

        except Exception:
            logger.exception("Failed to set tenant schema for org_id=%s", organization_id)
            return None

        return None

    def process_response(self, request, response):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO %s", [self.PUBLIC_SCHEMA])
        except Exception:
            logger.exception("Failed to reset search_path")
        return response
