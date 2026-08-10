from __future__ import annotations

import logging

from django.db import connection
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import UntypedToken

from apps.platform.raw_repository import (
    get_cached_organization,
    invalidate_org_schema_cache,
)

logger = logging.getLogger(__name__)

__all__ = ["TenantMiddleware", "invalidate_org_schema_cache"]


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


class TenantMiddleware:
    """Points PostgreSQL's ``search_path`` at the caller's tenant schema.

    Expects a verified PMS JWT carrying ``user_type: "pms"`` and
    ``organization_id``; anything else is served from ``public``.

    The reset is in a ``finally`` rather than a ``process_response`` hook
    because ``search_path`` is connection state, not request state. If a view
    raised and the reset were skipped, the very next request handled by that
    connection would read the previous caller's schema — a cross-tenant leak.
    That is survivable today only because ``CONN_MAX_AGE`` defaults to 0 and
    Django drops the connection after each request.

    NOTE: this pattern is unsafe behind a transaction-pooling connection pooler
    (PgBouncer in `transaction` mode). ``SET`` runs outside a transaction, so
    the pooler may hand the mutated connection to a different client before the
    reset lands. Use session pooling, or move to ``SET LOCAL`` inside an
    explicit transaction, before putting one in front of this service.
    """

    PUBLIC_SCHEMA = "public"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        applied = self._apply_tenant_schema(request)
        try:
            return self.get_response(request)
        finally:
            if applied:
                self._reset_search_path()

    def _apply_tenant_schema(self, request) -> bool:
        organization_id = _extract_org_id_from_jwt(request)
        if not organization_id:
            return False

        try:
            org = get_cached_organization(organization_id)
            if not org or not org.get("schema_name"):
                logger.warning("No schema found for org_id=%s", organization_id)
                return False

            schema_name = org["schema_name"]

            if not _schema_exists(schema_name):
                logger.error(
                    "Tenant schema '%s' does not exist for org_id=%s",
                    schema_name,
                    organization_id,
                )
                # The cached row points at a schema that is gone; drop it so a
                # recreated organization is picked up without waiting out the TTL.
                invalidate_org_schema_cache(organization_id)
                return False

            request.organization = org
            request.schema_name = schema_name

            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO %s, public", [schema_name])
            return True

        except Exception:
            logger.exception("Failed to set tenant schema for org_id=%s", organization_id)
            # The SET may or may not have landed before the failure, so reset
            # rather than trusting the connection is still on `public`.
            self._reset_search_path()
            return False

    def _reset_search_path(self) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO %s", [self.PUBLIC_SCHEMA])
        except Exception:
            logger.exception("Failed to reset search_path")
