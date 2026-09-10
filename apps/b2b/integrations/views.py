"""The workspace's own integrations screen.

Anybody on the roster but a guest may open it and connect Meta; unplugging
and pausing are the managing roles' and the connector's — see
`permissions`. The two *public* halves of the flow (the
OAuth callback Meta redirects the browser to, and the webhook Meta posts leads
to) are in `public_views`, because neither carries a workspace login.
"""
from __future__ import annotations

import logging
import uuid

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.integrations import credentials, crypto, meta
from apps.b2b.integrations import repository as int_repo
from apps.b2b.integrations.permissions import (
    CanConnectMeta,
    may_manage_integrations,
    may_unplug_meta,
)
from apps.b2b.integrations.serializers import (
    IntegrationListSerializer,
    IntegrationSerializer,
    MetaConnectSerializer,
    PageToggleSerializer,
)
from apps.b2b.integrations.tasks import sync_meta_pages
from apps.b2b.models import IntegrationProvider, IntegrationStatus
from apps.b2b import repository as b2b_repo
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WorkspaceAPIView

logger = logging.getLogger(__name__)

INTEGRATIONS_TAG = ["B2B / Workspace (mobile)"]

#: How long the browser has to come back from Meta. Ten minutes is longer than
#: the login takes and short enough that a callback URL found in somebody's
#: browser history is worthless.
STATE_TTL = 600

_STATE_KEY = "b2b:integrations:meta:oauth:{}"


def state_key(state: str) -> str:
    return _STATE_KEY.format(state)


# ─── Payloads ─────────────────────────────────────────────────────────────────

def _page_payload(page: dict) -> dict:
    return {
        "id": page["id"],
        "page_id": page["page_id"],
        "page_name": page.get("page_name") or page["page_id"],
        "is_active": bool(page.get("is_active")),
        "subscribed": bool(page.get("subscribed")),
        "lead_count": page.get("lead_count") or 0,
        "last_lead_at": page.get("last_lead_at"),
        "last_error": page.get("last_error"),
    }


def meta_payload(company_id: int, viewer=None) -> dict:
    """Meta's row on the screen, connected or not, as `viewer` may act on it."""
    integration = int_repo.get_integration(company_id, IntegrationProvider.META)
    pages = int_repo.list_pages(company_id) if integration else []

    connected_by = None
    if integration and integration.get("connected_by_id"):
        employee = b2b_repo.get_employee(
            integration["connected_by_id"], company_id
        )
        connected_by = (employee or {}).get("full_name")

    status_value = (integration or {}).get("status") or IntegrationStatus.DISCONNECTED
    # A row with the token cleared is disconnected whatever the column says —
    # the two are written together, and reading the token is what the ingest
    # path actually depends on.
    if integration and not integration.get("access_token_enc"):
        status_value = IntegrationStatus.DISCONNECTED

    return {
        "provider": IntegrationProvider.META,
        "name": IntegrationProvider.LABELS[IntegrationProvider.META],
        "status": status_value,
        "connected": status_value == IntegrationStatus.CONNECTED,
        # Whether Weel's Facebook app is configured on this server at all.
        # The app draws a different row for "not set up" than for "you have
        # not connected it", because only one of the two is the user's to fix.
        "available": credentials.is_available(),
        "account_name": (integration or {}).get("account_name"),
        "connected_at": (integration or {}).get("connected_at"),
        "connected_by": connected_by,
        "last_sync_at": (integration or {}).get("last_sync_at"),
        "last_error": (integration or {}).get("last_error"),
        "lead_count": (integration or {}).get("lead_count") or 0,
        "token_expires_at": (integration or {}).get("token_expires_at"),
        # Nothing to paste anywhere any more; null for older app builds.
        "setup": None,
        "pages": [_page_payload(page) for page in pages],
        # Whether this viewer may unplug it or pause its pages — the managing
        # roles, or whoever connected it. See `permissions.may_unplug_meta`.
        "can_disconnect": bool(
            viewer is not None and may_unplug_meta(viewer, integration)
        ),
        "ai": None,
    }


def _refuse_unplug():
    return Response(
        {"detail": _(
            "Only the owner, an administrator, a manager or the person "
            "who connected Meta can do this."
        )},
        status=status.HTTP_403_FORBIDDEN,
    )


# ─── Views ────────────────────────────────────────────────────────────────────

class IntegrationsAPIView(WorkspaceAPIView):
    """Signed in, and on the roster as anything but a guest."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser, CanConnectMeta]


class IntegrationListView(IntegrationsAPIView):
    """GET /api/b2b/workspace/integrations/ — what can be connected, and what is."""

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="List integrations (anybody but a guest)",
        responses={200: IntegrationListSerializer()},
    )
    def get(self, request):
        from apps.b2b.integrations.ai_views import ai_payload

        company_id = request.user.company_id
        # The AI assistants' rows only for the roles whose keys they are: an
        # employee's screen is Meta alone, and the app draws whatever rows
        # come back.
        manages = may_manage_integrations(request.user.role)
        return Response({
            "results": [
                meta_payload(company_id, request.user),
                *([ai_payload(company_id, provider) for provider in IntegrationProvider.AI]
                  if manages else []),
            ],
            "can_manage": manages,
        })


class MetaConnectView(IntegrationsAPIView):
    """POST /api/b2b/workspace/integrations/meta/connect/ — start the login.

    Answers with a URL for the phone to open in its browser. The rest happens
    there and comes back through `public_views.MetaOAuthCallbackView`; the app
    polls the list endpoint when it returns to the foreground.

    The state is random and short-lived rather than the company id: it is what
    ties the callback to this workspace, and a guessable one would let anybody
    who found the callback URL attach *their* Facebook pages to somebody
    else's funnel.
    """

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Begin the Meta connection",
        responses={
            200: MetaConnectSerializer(),
            503: openapi.Response(description="Meta is not configured on this server"),
        },
    )
    def post(self, request):
        creds = credentials.global_credentials()
        if not credentials.is_available():
            return Response(
                {"detail": _("Meta is not configured on this server.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        state = uuid.uuid4().hex
        cache.set(
            state_key(state),
            {
                "company_id": request.user.company_id,
                "employee_id": request.user.id,
            },
            timeout=STATE_TTL,
        )
        try:
            url = meta.authorize_url(state, creds)
        except ImproperlyConfigured as exc:
            return Response({"detail": str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({
            "authorize_url": url, "state": state, "expires_in": STATE_TTL,
        })


class MetaDisconnectView(IntegrationsAPIView):
    """GET  /integrations/meta/ — this workspace's Meta connection.
    DELETE /integrations/meta/ — unplug it."""

    @swagger_auto_schema(tags=INTEGRATIONS_TAG,
                         operation_summary="The Meta connection",
                         responses={200: IntegrationSerializer()})
    def get(self, request):
        return Response(meta_payload(request.user.company_id, request.user))

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Disconnect Meta",
        responses={200: IntegrationSerializer()},
    )
    def delete(self, request):
        company_id = request.user.company_id
        integration = int_repo.get_integration(company_id, IntegrationProvider.META)
        if integration and not may_unplug_meta(request.user, integration):
            return _refuse_unplug()
        if integration:
            # Tell Meta to stop sending, then forget the tokens. In that order:
            # unsubscribing needs the page token, and a failure here must not
            # stop the disconnect — a workspace that pressed the button has to
            # end up disconnected whatever Facebook says.
            for page in int_repo.list_pages(company_id):
                try:
                    meta.unsubscribe_page(
                        page["page_id"], crypto.decrypt(page.get("access_token_enc"))
                    )
                except Exception:  # noqa: BLE001
                    logger.info("Could not unsubscribe page %s", page.get("page_id"))
            int_repo.delete_pages(integration["id"])
            int_repo.disconnect(company_id, IntegrationProvider.META)

        # The leads already on the board stay exactly as they are, marked
        # "Meta". They are real deals somebody may be working; unplugging the
        # source is not a reason to take them away.
        return Response(meta_payload(company_id, request.user))


class MetaPageView(IntegrationsAPIView):
    """PATCH /integrations/meta/pages/<id>/ — pause or resume one page."""

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Switch one page's ingest on or off",
        request_body=PageToggleSerializer,
        responses={200: IntegrationSerializer()},
    )
    def patch(self, request, page_row_id: int):
        serializer = PageToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        page = int_repo.get_page(page_row_id, request.user.company_id)
        if not page:
            return Response({"detail": _("Page not found.")},
                            status=status.HTTP_404_NOT_FOUND)
        # Pausing a page stops its leads as surely as unplugging does.
        integration = int_repo.get_integration_by_id(page["integration_id"])
        if not may_unplug_meta(request.user, integration):
            return _refuse_unplug()
        int_repo.set_page_active(
            page_row_id, request.user.company_id,
            serializer.validated_data["is_active"],
        )
        return Response(meta_payload(request.user.company_id, request.user))


class MetaSyncView(IntegrationsAPIView):
    """POST /integrations/meta/sync/ — fetch recent submissions now.

    The webhook is how leads arrive; this is the button for the gap it cannot
    cover — a subscription added after a campaign started, an hour our server
    was down. Queued rather than run inline: it walks every form on every page
    and the phone should not hold a request open for it.
    """

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Pull recent Meta leads now",
        responses={202: openapi.Response(description="Sync queued")},
    )
    def post(self, request):
        if not credentials.is_available():
            return Response(
                {"detail": _("Meta is not configured.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        integration = int_repo.get_integration(
            request.user.company_id, IntegrationProvider.META
        )
        if not integration or not integration.get("access_token_enc"):
            return Response({"detail": _("Meta is not connected.")},
                            status=status.HTTP_400_BAD_REQUEST)

        sync_meta_pages.delay(request.user.company_id)
        return Response({"detail": _("Checking Meta for new leads.")},
                        status=status.HTTP_202_ACCEPTED)
