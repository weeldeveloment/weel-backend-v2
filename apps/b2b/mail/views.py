"""Mail API, mounted under `/api/b2b/workspace/mail/`.

Everything subclasses ``WorkspaceAPIView``, so the same endpoints serve the
phone (a `b2b_employee` token) and the web dashboard (a `b2b` token bridged
onto an employee) without either knowing about the other.

One rule holds throughout, and it is stricter than anywhere else in this
codebase: **an account belongs to the person, not the company.** These rows are
somebody's private correspondence — often their personal Gmail — not company
data their colleagues or even the owner may read. So every lookup is scoped by
``request.user.id``, never by ``company_id``, and there is no administrative
endpoint that can reach another person's mail.
"""
from __future__ import annotations

import logging
import re
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.mail import crypto, oauth, providers, repository as repo
from apps.b2b.mail.connection import MailAuthError, verify
from apps.b2b.mail.sanitize import make_snippet, sanitize_html
from apps.b2b.mail.serializers import (
    B2BNotificationSerializer,
    MailAccountConnectSerializer,
    MailAccountPatchSerializer,
    MailAccountSerializer,
    MailMessageSerializer,
    MailProviderHintSerializer,
    MailSendSerializer,
    MailThreadFlagsSerializer,
    MailThreadSerializer,
    NotificationReadSerializer,
)
from apps.b2b.mail.tasks import send_mail_message, sync_one_account
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WorkspaceAPIView

logger = logging.getLogger(__name__)

MAIL_TAG = ["B2B / Mail"]


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _disabled_response():
    return Response(
        {"detail": _("Mail is not enabled for this installation.")},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _account_or_error(request, account_id: int | None):
    """Resolve the account a request is about, scoped to the caller.

    With no id, the caller's first connected account is used — the common case
    is exactly one, and making the clients pass an id they do not have yet
    would mean an extra round trip on every screen.
    """
    if not getattr(settings, "B2B_MAIL_ENABLED", False):
        return None, _disabled_response()

    if account_id is not None:
        account = repo.get_account(account_id, request.user.id)
        if account is None:
            return None, Response(
                {"detail": _("Mail account not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        return account, None

    accounts = repo.list_accounts(request.user.id)
    if not accounts:
        # Not an error the user can retry — they have simply not connected an
        # inbox yet — so it is reported as a state the apps render a "connect"
        # prompt for.
        return None, Response(
            {"detail": _("No mail account is connected."), "code": "no_account"},
            status=status.HTTP_404_NOT_FOUND,
        )
    return accounts[0], None


def _requested_account_id(request) -> int | None:
    try:
        return int(request.query_params["account_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _account_payload(account: dict) -> dict:
    return {
        **MailAccountSerializer(account).data,
        "unread": repo.total_unread(account["id"]),
    }


def _message_payload(message: dict, recipients: dict, attachments: dict) -> dict:
    return {
        "id": message["id"],
        "thread_id": message["thread_id"],
        "direction": message["direction"],
        "status": message["status"],
        "from_address": message["from_address"],
        "from_name": message["from_name"],
        "subject": message["subject"],
        "body_text": message["body_text"],
        # Renamed on the way out: the clients never see an unsanitised body, so
        # the column's `_sanitized` suffix is an implementation detail.
        "body_html": message["body_html_sanitized"],
        "has_attachments": message["has_attachments"],
        "is_read": message["is_read"],
        "error": message.get("error"),
        "sent_at": message.get("sent_at") or message.get("created_at"),
        "recipients": [
            {"kind": r["kind"], "address": r["address"], "name": r["name"]}
            for r in recipients.get(message["id"], [])
        ],
        "attachments": [
            {
                "id": a["id"],
                "filename": a["filename"],
                "content_type": a["content_type"],
                "size_bytes": a["size_bytes"],
                "download_url": f"/api/b2b/workspace/mail/attachments/{a['id']}/",
            }
            for a in attachments.get(message["id"], [])
        ],
    }


# ─── Connecting an inbox ──────────────────────────────────────────────────────

class MailAccountListCreateView(WorkspaceAPIView):
    """GET / POST /api/b2b/workspace/mail/accounts/ — the caller's own inboxes."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="List the inboxes this person has connected",
        responses={200: MailAccountSerializer(many=True)},
    )
    def get(self, request):
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return _disabled_response()

        accounts = repo.list_accounts(request.user.id)
        return Response({
            "results": [_account_payload(account) for account in accounts],
            "google_oauth_available": oauth.is_enabled(),
        })

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Connect an inbox with an app password",
        request_body=MailAccountConnectSerializer,
        responses={201: MailAccountSerializer()},
    )
    def post(self, request):
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return _disabled_response()

        serializer = MailAccountConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        address = data["address"]

        if repo.find_account(request.user.id, address):
            return Response(
                {"address": [_("This inbox is already connected.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        preset = providers.for_address(address)
        candidate = {
            "id": None,
            "address": address,
            "auth_type": "app_password",
            "secret_enc": crypto.encrypt(data["password"]),
            "imap_host": (data.get("imap_host") or preset.imap_host).strip(),
            "imap_port": data.get("imap_port") or preset.imap_port,
            "smtp_host": (data.get("smtp_host") or preset.smtp_host).strip(),
            "smtp_port": data.get("smtp_port") or preset.smtp_port,
        }

        # Proved before it is stored. An account that cannot log in would
        # otherwise sit in the list looking connected and silently never sync,
        # which is a far worse first experience than an error on this screen.
        try:
            verify(candidate)
        except MailAuthError as exc:
            hint = _("Check the password. %(provider)s requires an app password, "
                     "not your normal one.") % {"provider": preset.label}
            return Response(
                {"detail": str(exc),
                 "hint": hint if preset.requires_app_password else None,
                 "help_url": preset.help_url},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ConnectionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        account = repo.create_account(
            company_id=request.user.company_id,
            employee_id=request.user.id,
            address=address,
            display_name=data.get("display_name") or request.user.full_name or address,
            provider=preset.key,
            auth_type="app_password",
            secret_enc=candidate["secret_enc"],
            imap_host=candidate["imap_host"],
            imap_port=candidate["imap_port"],
            smtp_host=candidate["smtp_host"],
            smtp_port=candidate["smtp_port"],
        )
        if account is None:
            return Response({"detail": _("Could not save the account.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # First sync straight away, so the inbox is not empty when they look.
        sync_one_account.delay(account["id"])
        return Response(_account_payload(account), status=status.HTTP_201_CREATED)


class MailProviderHintView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/providers/?address=… — what to show next.

    Lets the connect screen say "Gmail needs an app password, here is the link"
    the moment an address is typed, instead of after a failed attempt.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Guess a provider's settings from an address",
        manual_parameters=[
            openapi.Parameter("address", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              required=True),
        ],
        responses={200: MailProviderHintSerializer()},
    )
    def get(self, request):
        address = (request.query_params.get("address") or "").strip().lower()
        hint = providers.describe(address)
        hint["supports_oauth"] = hint["supports_oauth"] and oauth.is_enabled()
        return Response(hint)


class MailAccountDetailView(WorkspaceAPIView):
    """PATCH / DELETE /api/b2b/workspace/mail/accounts/<id>/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Rename a connected inbox",
                         request_body=MailAccountPatchSerializer,
                         responses={200: MailAccountSerializer()})
    def patch(self, request, account_id: int):
        account, error = _account_or_error(request, account_id)
        if error:
            return error

        serializer = MailAccountPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data:
            return Response(_account_payload(account))

        updated = repo.update_account(account["id"], **serializer.validated_data)
        return Response(_account_payload(updated or account))

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Disconnect an inbox",
                         responses={204: openapi.Response(description="Disconnected")})
    def delete(self, request, account_id: int):
        account, error = _account_or_error(request, account_id)
        if error:
            return error

        # The stored copy of the mail goes with it. Deliberate: somebody
        # removing their personal Gmail from a work app is asking for it to be
        # gone, not archived somewhere their employer could still reach.
        repo.delete_account(account["id"], request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MailAccountReconnectView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/accounts/<id>/reconnect/ — new password."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Replace a stored app password",
                         request_body=MailAccountConnectSerializer,
                         responses={200: MailAccountSerializer()})
    def post(self, request, account_id: int):
        account, error = _account_or_error(request, account_id)
        if error:
            return error

        serializer = MailAccountConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        candidate = {
            **account,
            "auth_type": "app_password",
            "secret_enc": crypto.encrypt(data["password"]),
        }
        try:
            verify(candidate)
        except MailAuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        updated = repo.update_account(
            account["id"],
            secret_enc=candidate["secret_enc"],
            auth_type="app_password",
            is_active=True,
            sync_error=None,
        )
        sync_one_account.delay(account["id"])
        return Response(_account_payload(updated or account))


class MailGoogleStartView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/oauth/google/ — where to send the browser."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Begin Google sign-in",
                         responses={200: openapi.Response(description="Authorize URL")})
    def get(self, request):
        if not oauth.is_enabled():
            return Response(
                {"detail": _("Google sign-in is not available yet. Connect with an "
                             "app password instead.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # The state is what ties the callback back to this employee. It is
        # random and short-lived rather than the employee id, so a leaked
        # callback URL cannot be replayed to attach an inbox to someone else.
        from django.core.cache import cache

        state = uuid.uuid4().hex
        cache.set(f"b2b:mail:oauth:{state}", request.user.id, timeout=600)
        return Response({"authorize_url": oauth.authorize_url(state)})


class MailGoogleCallbackView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/oauth/google/callback/ — finish sign-in."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Complete Google sign-in",
                         responses={201: MailAccountSerializer()})
    def post(self, request):
        from django.core.cache import cache

        if not oauth.is_enabled():
            return Response({"detail": _("Google sign-in is not available.")},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        code = (request.data.get("code") or "").strip()
        state = (request.data.get("state") or "").strip()
        if not code or not state:
            return Response({"detail": _("Missing code or state.")},
                            status=status.HTTP_400_BAD_REQUEST)

        owner_id = cache.get(f"b2b:mail:oauth:{state}")
        if owner_id != request.user.id:
            # Either expired or issued to somebody else. Both are refused the
            # same way, so nothing about the other case leaks.
            return Response({"detail": _("This sign-in has expired. Try again.")},
                            status=status.HTTP_400_BAD_REQUEST)
        cache.delete(f"b2b:mail:oauth:{state}")

        try:
            granted = oauth.exchange_code(code)
        except (oauth.OAuthError, ImproperlyConfigured) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        address = granted["address"]
        if not address:
            return Response({"detail": _("Google did not return an address.")},
                            status=status.HTTP_400_BAD_REQUEST)

        preset = providers.for_address(address)
        existing = repo.find_account(request.user.id, address)
        fields = {
            "auth_type": "oauth",
            "provider": preset.key,
            "secret_enc": crypto.encrypt(granted["refresh_token"]),
            "oauth_access_enc": crypto.encrypt(granted["access_token"]),
            "oauth_expires_at": granted["expires_at"],
            "imap_host": preset.imap_host,
            "imap_port": preset.imap_port,
            "smtp_host": preset.smtp_host,
            "smtp_port": preset.smtp_port,
            "is_active": True,
            "sync_error": None,
        }

        # Signing in again with an inbox that is already connected upgrades it
        # rather than failing — that is exactly how somebody fixes an account
        # whose app password stopped working.
        if existing:
            account = repo.update_account(existing["id"], **fields)
        else:
            account = repo.create_account(
                company_id=request.user.company_id,
                employee_id=request.user.id,
                address=address,
                display_name=request.user.full_name or address,
                **{k: v for k, v in fields.items() if k not in ("is_active", "sync_error")},
            )
        if account is None:
            return Response({"detail": _("Could not save the account.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        sync_one_account.delay(account["id"])
        return Response(_account_payload(account), status=status.HTTP_201_CREATED)


class MailSyncNowView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/sync/ — pull-to-refresh."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Check for new mail now",
                         responses={202: openapi.Response(description="Sync queued")})
    def post(self, request):
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return _disabled_response()

        accounts = repo.list_accounts(request.user.id)
        for account in accounts:
            if account["is_active"]:
                sync_one_account.delay(account["id"])
        return Response({"detail": _("Checking for new mail.")}, status=status.HTTP_202_ACCEPTED)


# ─── Threads ──────────────────────────────────────────────────────────────────

class MailThreadListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/threads/ — the conversation list.

    With no ``account_id`` the caller's accounts are merged into one list, so
    somebody with a personal and a work inbox sees a single stream.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="List mail threads",
        manual_parameters=[
            openapi.Parameter("account_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                              description="Omit to merge every connected inbox"),
            openapi.Parameter("folder", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              description="inbox | sent | archive | trash"),
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("unread", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("starred", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: MailThreadSerializer(many=True)},
    )
    def get(self, request):
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return _disabled_response()

        accounts = repo.list_accounts(request.user.id)
        if not accounts:
            return Response({"results": [], "has_more": False, "total_unread": 0,
                             "code": "no_account"})

        requested = _requested_account_id(request)
        if requested is not None:
            accounts = [a for a in accounts if a["id"] == requested]
            if not accounts:
                return Response({"detail": _("Mail account not found.")},
                                status=status.HTTP_404_NOT_FOUND)

        folder = request.query_params.get("folder", "inbox")
        if folder not in {"inbox", "sent", "archive", "trash"}:
            folder = "inbox"
        try:
            limit = min(int(request.query_params.get("limit", 30)), 100)
        except (TypeError, ValueError):
            limit = 30

        threads = repo.list_threads_for_accounts(
            [account["id"] for account in accounts],
            folder=folder,
            query=(request.query_params.get("q") or "").strip() or None,
            unread_only=request.query_params.get("unread") in ("1", "true", "True"),
            starred_only=request.query_params.get("starred") in ("1", "true", "True"),
            limit=limit,
        )
        return Response({
            "results": MailThreadSerializer(threads, many=True).data,
            "has_more": len(threads) == limit,
            "total_unread": sum(repo.total_unread(a["id"]) for a in accounts),
        })


class MailThreadMessagesView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/threads/<id>/messages/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Messages in a thread (oldest first)",
        manual_parameters=[
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: MailMessageSerializer(many=True)},
    )
    def get(self, request, thread_id: int):
        thread = repo.get_thread_for_employee(thread_id, request.user.id)
        if thread is None:
            return Response({"detail": _("Conversation not found.")},
                            status=status.HTTP_404_NOT_FOUND)

        try:
            limit = min(int(request.query_params.get("limit", 30)), 100)
        except (TypeError, ValueError):
            limit = 30
        try:
            before_id = int(request.query_params["before_id"])
        except (KeyError, TypeError, ValueError):
            before_id = None

        messages = repo.list_messages(
            thread_id, thread["account_id"], before_id=before_id, limit=limit
        )
        ids = [message["id"] for message in messages]

        # Opening a thread is what marks it read, exactly as chat rooms work.
        # Only the newest page counts — paging back through history must not
        # clear messages that arrived since. The path is exempt from the GET
        # cache (core/middleware/cache.py) so this write is never skipped.
        if before_id is None:
            repo.mark_thread_read(thread_id, thread["account_id"])

        recipients = repo.list_recipients(ids)
        attachments = repo.list_attachments(ids)
        return Response({
            "results": [_message_payload(m, recipients, attachments) for m in messages],
            "has_more": len(messages) == limit,
        })


class MailThreadFlagsView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/threads/<id>/flags/ — star, archive, trash."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Star or move a thread",
                         request_body=MailThreadFlagsSerializer,
                         responses={200: MailThreadSerializer()})
    def post(self, request, thread_id: int):
        thread = repo.get_thread_for_employee(thread_id, request.user.id)
        if thread is None:
            return Response({"detail": _("Conversation not found.")},
                            status=status.HTTP_404_NOT_FOUND)

        serializer = MailThreadFlagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = repo.set_thread_flags(
            thread_id, thread["account_id"], **serializer.validated_data
        )
        return Response(MailThreadSerializer(updated or thread).data)


class MailThreadReadView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/threads/<id>/read/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Mark a thread as read",
                         responses={200: openapi.Response(description="Marked read")})
    def post(self, request, thread_id: int):
        thread = repo.get_thread_for_employee(thread_id, request.user.id)
        if thread is None:
            return Response({"detail": _("Conversation not found.")},
                            status=status.HTTP_404_NOT_FOUND)
        repo.mark_thread_read(thread_id, thread["account_id"])
        return Response({"detail": _("Marked as read")})


# ─── Sending ──────────────────────────────────────────────────────────────────

class MailSendView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/messages/ — compose and send."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Send an email",
                         request_body=MailSendSerializer,
                         responses={201: MailMessageSerializer()})
    def post(self, request):
        serializer = MailSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        account, error = _account_or_error(request, data.get("account_id"))
        if error:
            return error
        if not account["is_active"]:
            return Response(
                {"detail": _("This inbox needs to be reconnected."), "code": "reconnect"},
                status=status.HTTP_409_CONFLICT,
            )

        # The daily ceiling is the backstop against a stolen credential quietly
        # sending spam from somebody's real address — which would cost them
        # their own account, not just ours.
        limit = getattr(settings, "B2B_MAIL_DAILY_SEND_LIMIT", 200)
        if repo.count_sent_today(account["id"]) >= limit:
            return Response(
                {"detail": _("You have reached today's sending limit for this inbox.")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        parent = None
        if data.get("reply_to_message_id"):
            parent = repo.get_message(data["reply_to_message_id"], account["id"])
            if parent is None:
                return Response({"reply_to_message_id": [_("Message not found.")]},
                                status=status.HTTP_400_BAD_REQUEST)

        subject = data["subject"].strip()
        if parent and not subject:
            base = parent["subject"] or ""
            subject = base if base.lower().startswith("re:") else f"Re: {base}".strip()

        # The sender's own HTML is sanitised too. It is composed in our UI, but
        # trusting "our own" client is how stored XSS reaches the recipient's
        # copy — and their copy is rendered by our other client.
        body_html = (
            sanitize_html(data["body_html"], block_remote_images=False)
            if data["body_html"] else ""
        )
        body_text = data["body_text"].strip()

        thread = (
            repo.get_thread(parent["thread_id"], account["id"]) if parent else None
        )
        if thread is None:
            thread = repo.create_thread(
                account_id=account["id"],
                subject=subject or "(mavzusiz)",
                folder="sent",
                participants=", ".join(data["to"]),
                snippet=make_snippet(body_text),
                last_message_at=timezone.now(),
            )
        if thread is None:
            return Response({"detail": _("Could not start the conversation.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        message = repo.create_message(
            thread_id=thread["id"],
            account_id=account["id"],
            direction="outbound",
            # Recorded as queued, not sent: the actual submission happens in a
            # task, and the UI shows this state until it reports back.
            status="queued",
            in_reply_to=parent["message_id_header"] if parent else None,
            references_header=parent["references_header"] if parent else None,
            from_address=account["address"],
            from_name=account.get("display_name") or "",
            subject=subject,
            body_text=body_text,
            body_html_sanitized=body_html,
            is_read=True,
            sent_at=timezone.now(),
        )
        if message is None:
            return Response({"detail": _("Could not save the message.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        repo.add_recipients(message["id"], [
            (kind, address, "")
            for kind in ("to", "cc", "bcc")
            for address in data[kind]
        ])

        claimed = repo.claim_attachments(data["attachment_ids"], account["id"], message["id"])
        if claimed:
            repo.update_message(message["id"], has_attachments=True)

        entry = repo.create_outbox_entry(account["id"], message["id"], {
            "to": data["to"],
            "cc": data["cc"],
            "bcc": data["bcc"],
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "in_reply_to": parent["message_id_header"] if parent else None,
            "references": parent["references_header"] if parent else None,
        })
        repo.refresh_thread_counters(thread["id"])

        if entry:
            send_mail_message.delay(entry["id"])

        stored = repo.get_message(message["id"], account["id"])
        recipients = repo.list_recipients([message["id"]])
        attachments = repo.list_attachments([message["id"]])
        return Response(
            _message_payload(stored or message, recipients, attachments),
            status=status.HTTP_201_CREATED,
        )


class MailAttachmentUploadView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/attachments/ — upload before composing."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Upload a file to attach to a message",
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
        ],
        responses={201: openapi.Response(description="Uploaded")},
    )
    def post(self, request):
        account, error = _account_or_error(request, _requested_account_id(request))
        if error:
            return error

        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": [_("No file was sent.")]},
                            status=status.HTTP_400_BAD_REQUEST)

        max_mb = getattr(settings, "B2B_MAIL_MAX_ATTACHMENT_MB", 20)
        if upload.size > max_mb * 1024 * 1024:
            return Response(
                {"file": [_("Files must be smaller than %(n)d MB.") % {"n": max_mb}]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The client-supplied name is stored for display but never used to
        # build the path.
        safe_name = re.sub(r"[^\w.\-]", "_", upload.name)[:120] or "file"
        storage_key = f"b2b/mail/{account['id']}/outgoing/{uuid.uuid4().hex}/{safe_name}"
        default_storage.save(storage_key, upload)

        attachment = repo.create_attachment(
            account_id=account["id"],
            message_id=None,
            filename=upload.name,
            content_type=upload.content_type or "application/octet-stream",
            size_bytes=upload.size,
            storage_key=storage_key,
        )
        return Response({
            "id": attachment["id"],
            "filename": attachment["filename"],
            "content_type": attachment["content_type"],
            "size_bytes": attachment["size_bytes"],
        }, status=status.HTTP_201_CREATED)


class MailAttachmentDownloadView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/attachments/<id>/ — redirect to the file."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Download an attachment",
                         responses={302: openapi.Response(description="Redirect to the file")})
    def get(self, request, attachment_id: int):
        from django.http import HttpResponseRedirect

        attachment = repo.get_attachment_for_employee(attachment_id, request.user.id)
        if attachment is None:
            return Response({"detail": _("File not found.")}, status=status.HTTP_404_NOT_FOUND)
        return HttpResponseRedirect(default_storage.url(attachment["storage_key"]))


# ─── Notifications ────────────────────────────────────────────────────────────

class B2BNotificationListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/notifications/ — the real, server-stored feed.

    Replaces the feed both clients used to synthesise from whatever data they
    happened to have loaded, which could not show anything that arrived while
    the app was closed.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="List notifications",
        manual_parameters=[
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: B2BNotificationSerializer(many=True)},
    )
    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 30)), 100)
        except (TypeError, ValueError):
            limit = 30
        try:
            before_id = int(request.query_params["before_id"])
        except (KeyError, TypeError, ValueError):
            before_id = None

        rows = repo.list_notifications(request.user.id, before_id=before_id, limit=limit)
        return Response({
            "results": B2BNotificationSerializer(rows, many=True).data,
            "has_more": len(rows) == limit,
            "unread": repo.unread_notification_count(request.user.id),
        })


class B2BNotificationReadView(WorkspaceAPIView):
    """POST /api/b2b/workspace/notifications/read/ — mark some or all read."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Mark notifications read",
                         request_body=NotificationReadSerializer,
                         responses={200: openapi.Response(description="Marked read")})
    def post(self, request):
        serializer = NotificationReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = repo.mark_notifications_read(
            request.user.id, serializer.validated_data.get("ids") or None
        )
        return Response({
            "updated": updated,
            "unread": repo.unread_notification_count(request.user.id),
        })
