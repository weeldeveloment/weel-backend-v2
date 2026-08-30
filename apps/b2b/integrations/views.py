"""The workspace's own integrations screen.

Every endpoint here is the owner's or the administrator's — see
`permissions.CanManageIntegrations`. The two *public* halves of the flow (the
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
from apps.b2b.integrations.permissions import CanManageIntegrations
from apps.b2b.integrations.serializers import (
    IntegrationListSerializer,
    IntegrationSerializer,
    MetaAppSerializer,
    MetaConnectSerializer,
    MetaSetupSerializer,
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


def public_url(request, name: str) -> str:
    """An absolute URL to one of our own public endpoints.

    Built from the request rather than from a setting so a workspace pasting
    the webhook into *their* Facebook app is given the host they are actually
    talking to. A deployment behind a proxy that rewrites the host should set
    the setting; where one exists it wins.
    """
    from django.urls import reverse

    return request.build_absolute_uri(reverse(name))


def _setup_payload(request, creds) -> dict:
    """The three values the owner has to paste into a Facebook app.

    Shown for both models. Even a workspace on our app needs the redirect URI
    to make sense of an error, and a workspace on their own cannot connect at
    all without all three — so the screen prints them with a copy button
    instead of sending somebody to a document.
    """
    redirect_uri = creds.redirect_uri or public_url(request, "b2b-meta-callback")
    return {
        "uses_own_app": creds.is_own,
        "app_id": creds.app_id or None,
        "redirect_uri": redirect_uri,
        "webhook_url": public_url(request, "b2b-meta-webhook"),
        # Only meaningful to whoever configures the app, which is the person
        # reading this — and this endpoint is the owner's alone.
        "verify_token": creds.verify_token or None,
    }


def meta_payload(company_id: int, request=None) -> dict:
    """Meta's row on the screen, connected or not."""
    integration = int_repo.get_integration(company_id, IntegrationProvider.META)
    pages = int_repo.list_pages(company_id) if integration else []
    creds = credentials.from_integration(integration) or credentials.global_credentials()

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
        # Whether a connection can be offered at all — through our app or
        # through theirs. The app draws a different row for "not set up" than
        # for "you have not connected it", because only one of the two is the
        # user's to fix, and with their own app it becomes fixable.
        "available": credentials.is_available(company_id),
        "account_name": (integration or {}).get("account_name"),
        "connected_at": (integration or {}).get("connected_at"),
        "connected_by": connected_by,
        "last_sync_at": (integration or {}).get("last_sync_at"),
        "last_error": (integration or {}).get("last_error"),
        "lead_count": (integration or {}).get("lead_count") or 0,
        "token_expires_at": (integration or {}).get("token_expires_at"),
        "setup": _setup_payload(request, creds) if request is not None else None,
        "pages": [_page_payload(page) for page in pages],
    }


# ─── Views ────────────────────────────────────────────────────────────────────

class IntegrationsAPIView(WorkspaceAPIView):
    """Signed in, and holding the owner's or administrator's role."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser, CanManageIntegrations]


class IntegrationListView(IntegrationsAPIView):
    """GET /api/b2b/workspace/integrations/ — what can be connected, and what is."""

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="List integrations (owner/administrator only)",
        responses={200: IntegrationListSerializer()},
    )
    def get(self, request):
        return Response({
            "results": [meta_payload(request.user.company_id, request)],
            "can_manage": True,
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
        creds = credentials.for_company(request.user.company_id)
        if not credentials.is_available(request.user.company_id):
            return Response(
                {"detail": _(
                    "Meta is not configured. Add this workspace's own Facebook "
                    "app, or ask an administrator to configure the server's."
                )},
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
        return Response(meta_payload(request.user.company_id, request))

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Disconnect Meta",
        responses={200: IntegrationSerializer()},
    )
    def delete(self, request):
        company_id = request.user.company_id
        integration = int_repo.get_integration(company_id, IntegrationProvider.META)
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
        return Response(meta_payload(company_id, request))


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
        int_repo.set_page_active(
            page_row_id, request.user.company_id,
            serializer.validated_data["is_active"],
        )
        return Response(meta_payload(request.user.company_id, request))


class MetaAppView(IntegrationsAPIView):
    """GET    /integrations/meta/app/ — what to paste into the Facebook app.
    PUT    /integrations/meta/app/ — connect through *this workspace's* app.
    DELETE /integrations/meta/app/ — go back to the deployment's app.

    The second path, and why it exists: while our own Facebook app is in
    Meta's review only its listed testers can authorise it, and some customers
    will not let their advertising data pass through an app they do not own.
    Both are real, so a workspace may bring its own — and everything below
    this line stops caring which, because `credentials.for_company` is the one
    place that decides.
    """

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="What to configure in the Facebook app",
        responses={200: MetaSetupSerializer()},
    )
    def get(self, request):
        creds = credentials.for_company(request.user.company_id)
        return Response(_setup_payload(request, creds))

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Use this workspace's own Facebook app",
        request_body=MetaAppSerializer,
        responses={200: MetaSetupSerializer()},
    )
    def put(self, request):
        serializer = MetaAppSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not crypto.is_configured():
            # Refusing beats storing it in the clear. The whole reason a
            # workspace hands us its app secret is that we keep it safe.
            return Response(
                {"detail": _(
                    "This server cannot store credentials securely yet "
                    "(no encryption key configured)."
                )},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        verify_token = (data.get("verify_token") or "").strip()
        if not verify_token:
            # Keep the one already issued: changing it under a webhook that is
            # configured and working would break it for no reason.
            existing = int_repo.get_integration(
                request.user.company_id, IntegrationProvider.META
            )
            verify_token = (
                (existing or {}).get("webhook_verify_token") or ""
            ).strip() or credentials.new_verify_token()

        int_repo.set_company_app(
            company_id=request.user.company_id,
            provider=IntegrationProvider.META,
            app_id=data["app_id"],
            app_secret_enc=crypto.encrypt(data["app_secret"]),
            verify_token=verify_token,
        )
        creds = credentials.for_company(request.user.company_id)
        if not creds.is_own:
            # Stored, but unreadable — which in practice means the encryption
            # key changed under us. Better to say so than to let them press
            # "Ulash" against the wrong app.
            return Response(
                {"detail": _("The app was saved but could not be read back.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(_setup_payload(request, creds))

    @swagger_auto_schema(
        tags=INTEGRATIONS_TAG,
        operation_summary="Stop using this workspace's own app",
        responses={200: MetaSetupSerializer()},
    )
    def delete(self, request):
        company_id = request.user.company_id
        # The pages go with it. Their tokens were issued by the app being
        # removed and are worthless to any other one — leaving them would show
        # the workspace as connected while every call to Meta failed.
        integration = int_repo.get_integration(company_id, IntegrationProvider.META)
        if integration:
            int_repo.delete_pages(integration["id"])
        int_repo.clear_company_app(company_id, IntegrationProvider.META)
        return Response(
            _setup_payload(request, credentials.for_company(company_id))
        )


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
        if not credentials.is_available(request.user.company_id):
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
