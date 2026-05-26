from __future__ import annotations

import logging
from typing import Any

from django.utils.deprecation import MiddlewareMixin
from django.db import connection

from apps.platform.raw_repository import get_organization_by_id

logger = logging.getLogger(__name__)


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
        user = getattr(request, "user", None)
        if not user:
            return None

        user_dict = user if isinstance(user, dict) else {}
        user_type = user_dict.get("user_type")
        if user_type != "pms":
            return None

        organization_id = user_dict.get("organization_id")
        if not organization_id:
            return None

        try:
            org = get_organization_by_id(int(organization_id))
            if not org or not org.get("schema_name"):
                return None

            schema_name = org["schema_name"]
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
