"""Corporate mail API, mounted under `/api/b2b/workspace/mail/`.

Everything here subclasses ``WorkspaceAPIView``, so the same endpoints serve
the phone (a `b2b_employee` token) and the web dashboard (a `b2b` token
bridged onto an employee) without either knowing about the other.

Two rules hold throughout:

* The caller's mailbox is resolved from their own identity — never from a
  parameter. A request cannot name a mailbox it does not own.
* Threads and messages are then read *through* that mailbox id, so a guessed id
  from another company returns 404 rather than someone else's mail.
"""
from __future__ import annotations

import logging

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

from apps.b2b.mail import crypto, dns_checks, mailcow, repository as repo
from apps.b2b.mail.sanitize import make_snippet, sanitize_html
from apps.b2b.mail.serializers import (
    B2BNotificationSerializer,
    MailboxCreateSerializer,
    MailboxCredentialSerializer,
    MailboxPatchSerializer,
    MailboxSerializer,
    MailDomainCreateSerializer,
    MailDomainSerializer,
    MailMessageSerializer,
    MailSendSerializer,
    MailThreadFlagsSerializer,
    MailThreadSerializer,
    NotificationReadSerializer,
)
from apps.b2b.mail.tasks import send_mail_message, sync_one_mailbox
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WorkspaceAPIView

logger = logging.getLogger(__name__)

MAIL_TAG = ["B2B / Corporate mail"]


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _mailbox_or_error(request):
    """The caller's own mailbox, or a Response explaining why there isn't one."""
    if not getattr(settings, "B2B_MAIL_ENABLED", False):
        return None, Response(
            {"detail": _("Corporate mail is not enabled for this installation.")},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    mailbox = repo.get_mailbox_for_employee(request.user.id)
    if mailbox is None:
        # Not an error the user can fix — their owner has to give them one — so
        # it is reported as a normal state the app renders an empty screen for.
        return None, Response(
            {"detail": _("You do not have a corporate mailbox yet."), "code": "no_mailbox"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if not mailbox["is_active"]:
        return None, Response(
            {"detail": _("Your mailbox is disabled."), "code": "mailbox_disabled"},
            status=status.HTTP_403_FORBIDDEN,
        )
    return mailbox, None


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


# ─── Mailbox: the caller's own ────────────────────────────────────────────────

class MailMeView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/me/ — do I have a mailbox, and what's in it?"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="The signed-in employee's mailbox",
        responses={200: MailboxSerializer()},
    )
    def get(self, request):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error
        return Response({
            **MailboxSerializer(mailbox).data,
            "unread": repo.total_unread(mailbox["id"]),
            "folders": repo.folder_counts(mailbox["id"]),
        })


class MailSyncNowView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/sync/ — pull-to-refresh.

    The beat sync already runs every minute; this exists so that pulling down
    on the inbox feels like it did something rather than waiting out the tick.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Check for new mail now",
                         responses={202: openapi.Response(description="Sync queued")})
    def post(self, request):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error
        sync_one_mailbox.delay(mailbox["id"])
        return Response({"detail": _("Checking for new mail.")}, status=status.HTTP_202_ACCEPTED)


# ─── Threads ──────────────────────────────────────────────────────────────────

class MailThreadListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/threads/ — the inbox list."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="List mail threads",
        manual_parameters=[
            openapi.Parameter("folder", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              description="inbox | sent | archive | trash"),
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("unread", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("starred", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: MailThreadSerializer(many=True)},
    )
    def get(self, request):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error

        folder = request.query_params.get("folder", "inbox")
        if folder not in {"inbox", "sent", "archive", "trash"}:
            folder = "inbox"
        try:
            limit = min(int(request.query_params.get("limit", 30)), 100)
        except (TypeError, ValueError):
            limit = 30
        try:
            before_id = int(request.query_params["before_id"])
        except (KeyError, TypeError, ValueError):
            before_id = None

        threads = repo.list_threads(
            mailbox["id"],
            folder=folder,
            query=(request.query_params.get("q") or "").strip() or None,
            unread_only=request.query_params.get("unread") in ("1", "true", "True"),
            starred_only=request.query_params.get("starred") in ("1", "true", "True"),
            before_id=before_id,
            limit=limit,
        )
        return Response({
            "results": MailThreadSerializer(threads, many=True).data,
            "has_more": len(threads) == limit,
            "total_unread": repo.total_unread(mailbox["id"]),
        })


class MailThreadMessagesView(WorkspaceAPIView):
    """GET /api/b2b/workspace/mail/threads/<id>/messages/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Messages in a thread (oldest first, paged from the newest end)",
        manual_parameters=[
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: MailMessageSerializer(many=True)},
    )
    def get(self, request, thread_id: int):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error
        if not repo.get_thread(thread_id, mailbox["id"]):
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

        messages = repo.list_messages(thread_id, mailbox["id"], before_id=before_id, limit=limit)
        ids = [message["id"] for message in messages]

        # Opening a thread is what marks it read, exactly as chat rooms work.
        # Only the newest page counts — paging back through history must not
        # clear messages that arrived since. The path is exempt from the GET
        # cache (core/middleware/cache.py) so this write is never skipped.
        if before_id is None:
            repo.mark_thread_read(thread_id, mailbox["id"])

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
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error
        if not repo.get_thread(thread_id, mailbox["id"]):
            return Response({"detail": _("Conversation not found.")},
                            status=status.HTTP_404_NOT_FOUND)

        serializer = MailThreadFlagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        thread = repo.set_thread_flags(thread_id, mailbox["id"], **serializer.validated_data)
        return Response(MailThreadSerializer(thread).data)


class MailThreadReadView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/threads/<id>/read/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Mark a thread as read",
                         responses={200: openapi.Response(description="Marked read")})
    def post(self, request, thread_id: int):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error
        if not repo.get_thread(thread_id, mailbox["id"]):
            return Response({"detail": _("Conversation not found.")},
                            status=status.HTTP_404_NOT_FOUND)
        repo.mark_thread_read(thread_id, mailbox["id"])
        return Response({"detail": _("Marked as read")})


# ─── Sending ──────────────────────────────────────────────────────────────────

class MailSendView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/messages/ — compose and send."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Send an email",
                         request_body=MailSendSerializer,
                         responses={201: MailMessageSerializer()})
    def post(self, request):
        mailbox, error = _mailbox_or_error(request)
        if error:
            return error

        serializer = MailSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        internal = set(repo.list_company_addresses(mailbox["company_id"]))
        outside = [
            address for address in (*data["to"], *data["cc"], *data["bcc"])
            if address not in internal
        ]
        if outside and not request.user.capabilities.get("can_send_external_mail"):
            return Response(
                {"detail": _("Your role may only write to colleagues inside the company.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        # The daily ceiling is the backstop against a stolen password quietly
        # turning this mailbox into a spam source overnight — which would get
        # the whole platform's sending IP listed, not just this company.
        if repo.count_sent_today(mailbox["id"]) >= mailbox["daily_send_limit"]:
            return Response(
                {"detail": _("You have reached today's sending limit for this mailbox.")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        parent = None
        if data.get("reply_to_message_id"):
            parent = repo.get_message(data["reply_to_message_id"], mailbox["id"])
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
        body_html = sanitize_html(data["body_html"], block_remote_images=False) if data["body_html"] else ""
        body_text = data["body_text"].strip()

        thread = repo.get_thread(parent["thread_id"], mailbox["id"]) if parent else None
        if thread is None:
            thread = repo.create_thread(
                mailbox_id=mailbox["id"],
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
            mailbox_id=mailbox["id"],
            direction="outbound",
            # Recorded as queued, not sent: the actual submission happens in a
            # task, and the UI shows this state until it reports back.
            status="queued",
            in_reply_to=parent["message_id_header"] if parent else None,
            references_header=parent["references_header"] if parent else None,
            from_address=mailbox["address"],
            from_name=mailbox.get("display_name") or mailbox.get("employee_name") or "",
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

        claimed = repo.claim_attachments(data["attachment_ids"], mailbox["id"], message["id"])
        if claimed:
            repo.update_message(message["id"], has_attachments=True)

        entry = repo.create_outbox_entry(mailbox["id"], message["id"], {
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

        stored = repo.get_message(message["id"], mailbox["id"])
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
        import re
        import uuid

        mailbox, error = _mailbox_or_error(request)
        if error:
            return error

        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": [_("No file was sent.")]}, status=status.HTTP_400_BAD_REQUEST)

        max_mb = getattr(settings, "B2B_MAIL_MAX_ATTACHMENT_MB", 20)
        if upload.size > max_mb * 1024 * 1024:
            return Response(
                {"file": [_("Files must be smaller than %(n)d MB.") % {"n": max_mb}]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The client-supplied name is stored for display but never used to
        # build the path.
        safe_name = re.sub(r"[^\w.\-]", "_", upload.name)[:120] or "file"
        storage_key = f"b2b/mail/{mailbox['id']}/outgoing/{uuid.uuid4().hex}/{safe_name}"
        default_storage.save(storage_key, upload)

        attachment = repo.create_attachment(
            mailbox_id=mailbox["id"],
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
    """GET /api/b2b/workspace/mail/attachments/<id>/ — redirect to the stored file."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Download an attachment",
                         responses={302: openapi.Response(description="Redirect to the file")})
    def get(self, request, attachment_id: int):
        from django.http import HttpResponseRedirect

        mailbox, error = _mailbox_or_error(request)
        if error:
            return error

        attachment = repo.get_attachment(attachment_id, mailbox["id"])
        if attachment is None:
            return Response({"detail": _("File not found.")}, status=status.HTTP_404_NOT_FOUND)
        return HttpResponseRedirect(default_storage.url(attachment["storage_key"]))


# ─── Owner: domains ───────────────────────────────────────────────────────────

class MailDomainListCreateView(WorkspaceAPIView):
    """GET / POST /api/b2b/workspace/mail/domains/ — the company's mail domains."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_capability = "can_manage_mail_domain"

    def _guard(self, request):
        if not request.user.capabilities.get("can_manage_mail_domain"):
            return Response({"detail": _("Only the company owner can manage mail domains.")},
                            status=status.HTTP_403_FORBIDDEN)
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return Response({"detail": _("Corporate mail is not enabled.")},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return None

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="List mail domains",
                         responses={200: MailDomainSerializer(many=True)})
    def get(self, request):
        guard = self._guard(request)
        if guard:
            return guard

        domains = repo.list_domains(request.user.company_id)
        return Response({"results": [self._payload(domain) for domain in domains]})

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Connect a domain",
                         request_body=MailDomainCreateSerializer,
                         responses={201: MailDomainSerializer()})
    def post(self, request):
        guard = self._guard(request)
        if guard:
            return guard

        serializer = MailDomainCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["domain"]

        # Globally unique, not per-company: two companies cannot both own the
        # delivery of one domain, and letting the second one try would silently
        # hand it their competitor's mail.
        if repo.find_domain_by_name(name):
            return Response({"domain": [_("This domain is already connected.")]},
                            status=status.HTTP_400_BAD_REQUEST)

        selector = getattr(settings, "B2B_MAIL_DKIM_SELECTOR", "weel")
        domain = repo.create_domain(request.user.company_id, name, selector)
        if domain is None:
            return Response({"detail": _("Could not save the domain.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            mailcow.add_domain(name)
            mailcow.add_dkim(name, selector)
            dkim = mailcow.get_dkim(name)
        except (mailcow.MailcowError, ImproperlyConfigured) as exc:
            # The row is kept with the error attached rather than rolled back,
            # so the owner sees what went wrong and can retry instead of the
            # domain silently vanishing from their settings screen.
            logger.exception("Mailcow provisioning failed for %s", name)
            repo.update_domain(domain["id"], last_error=str(exc)[:500], status="error")
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        domain = repo.update_domain(
            domain["id"],
            dkim_public_key=(dkim or {}).get("record", ""),
            last_error=None,
        )
        return Response(self._payload(domain), status=status.HTTP_201_CREATED)

    @staticmethod
    def _payload(domain: dict) -> dict:
        return {
            **MailDomainSerializer(domain).data,
            "dns_records": dns_checks.expected_records(
                domain["domain"], domain["dkim_selector"], domain.get("dkim_public_key")
            ),
        }


class MailDomainVerifyView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/domains/<id>/verify/ — check the DNS now."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Re-check a domain's DNS records",
                         responses={200: MailDomainSerializer()})
    def post(self, request, domain_id: int):
        if not request.user.capabilities.get("can_manage_mail_domain"):
            return Response({"detail": _("Only the company owner can manage mail domains.")},
                            status=status.HTTP_403_FORBIDDEN)

        domain = repo.get_domain(domain_id, request.user.company_id)
        if domain is None:
            return Response({"detail": _("Domain not found.")}, status=status.HTTP_404_NOT_FOUND)

        result = dns_checks.check_domain(domain["domain"], domain["dkim_selector"])
        active = result["mx_ok"] and result["spf_ok"] and result["dkim_ok"]
        domain = repo.update_domain(
            domain_id,
            mx_ok=result["mx_ok"],
            spf_ok=result["spf_ok"],
            dkim_ok=result["dkim_ok"],
            dmarc_ok=result["dmarc_ok"],
            status="active" if active else "pending",
            last_checked_at=timezone.now(),
            verified_at=domain.get("verified_at") or (timezone.now() if active else None),
        )
        return Response({
            **MailDomainListCreateView._payload(domain),
            "found": result["found"],
        })


class MailDomainDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/mail/domains/<id>/ — disconnect a domain."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Disconnect a domain",
                         responses={204: openapi.Response(description="Disconnected")})
    def delete(self, request, domain_id: int):
        if not request.user.capabilities.get("can_manage_mail_domain"):
            return Response({"detail": _("Only the company owner can manage mail domains.")},
                            status=status.HTTP_403_FORBIDDEN)

        domain = repo.get_domain(domain_id, request.user.company_id)
        if domain is None:
            return Response({"detail": _("Domain not found.")}, status=status.HTTP_404_NOT_FOUND)

        mailboxes = [m for m in repo.list_mailboxes(request.user.company_id)
                     if m["domain_id"] == domain_id]
        if mailboxes:
            # Deleting the domain in Mailcow would destroy every mailbox under
            # it, and with them mail the company may still need. Make the owner
            # remove them deliberately, one at a time.
            return Response(
                {"detail": _("Remove the %(n)d mailboxes on this domain first.")
                 % {"n": len(mailboxes)}},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            mailcow.delete_domain(domain["domain"])
        except (mailcow.MailcowError, ImproperlyConfigured) as exc:
            logger.warning("Could not delete %s on the mail server: %s", domain["domain"], exc)

        repo.delete_domain(domain_id, request.user.company_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Owner: mailboxes ─────────────────────────────────────────────────────────

class MailboxListCreateView(WorkspaceAPIView):
    """GET / POST /api/b2b/workspace/mail/mailboxes/ — who has an address."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _guard(self, request):
        if not request.user.capabilities.get("can_manage_mailboxes"):
            return Response({"detail": _("Only the company owner can manage mailboxes.")},
                            status=status.HTTP_403_FORBIDDEN)
        if not getattr(settings, "B2B_MAIL_ENABLED", False):
            return Response({"detail": _("Corporate mail is not enabled.")},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return None

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="List company mailboxes",
                         responses={200: MailboxSerializer(many=True)})
    def get(self, request):
        guard = self._guard(request)
        if guard:
            return guard
        mailboxes = repo.list_mailboxes(request.user.company_id)
        return Response({"results": MailboxSerializer(mailboxes, many=True).data})

    @swagger_auto_schema(
        tags=MAIL_TAG,
        operation_summary="Give an employee a mailbox",
        request_body=MailboxCreateSerializer,
        responses={201: MailboxCredentialSerializer()},
    )
    def post(self, request):
        guard = self._guard(request)
        if guard:
            return guard

        serializer = MailboxCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        domain = repo.get_domain(data["domain_id"], request.user.company_id)
        if domain is None:
            return Response({"domain_id": [_("Domain not found.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if domain["status"] != "active":
            return Response(
                {"domain_id": [_("Publish the DNS records and verify the domain first.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee = repo.get_employee(data["employee_id"], request.user.company_id)
        if employee is None:
            return Response({"employee_id": [_("This employee is not in your company.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if repo.get_mailbox_for_employee(employee["id"]):
            return Response({"employee_id": [_("This employee already has a mailbox.")]},
                            status=status.HTTP_400_BAD_REQUEST)

        address = f"{data['local_part']}@{domain['domain']}"
        if repo.find_mailbox_by_address(address):
            return Response({"local_part": [_("This address is already taken.")]},
                            status=status.HTTP_400_BAD_REQUEST)

        display_name = data.get("display_name") or employee.get("full_name") or address
        password = crypto.generate_password()
        quota_mb = 2048

        try:
            mailcow.add_mailbox(
                local_part=data["local_part"],
                domain=domain["domain"],
                password=password,
                display_name=display_name,
                quota_mb=quota_mb,
            )
        except (mailcow.MailcowError, ImproperlyConfigured) as exc:
            logger.exception("Could not create mailbox %s", address)
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        mailbox = repo.create_mailbox(
            company_id=request.user.company_id,
            domain_id=domain["id"],
            employee_id=employee["id"],
            address=address,
            local_part=data["local_part"],
            display_name=display_name,
            smtp_password_enc=crypto.encrypt(password),
            quota_bytes=quota_mb * 1024 * 1024,
            daily_send_limit=getattr(settings, "B2B_MAIL_DAILY_SEND_LIMIT", 200),
        )
        if mailbox is None:
            # The mailbox exists upstream but we could not record it. Undo it,
            # otherwise the address is taken by a row nobody can see or reuse.
            try:
                mailcow.delete_mailbox(address)
            except mailcow.MailcowError:
                logger.exception("Orphaned mailbox %s on the mail server", address)
            return Response({"detail": _("Could not save the mailbox.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # The only response that ever carries the password. The owner passes it
        # to the employee, who needs it for Outlook or the Gmail app; our own
        # apps authenticate with the session token and never see it again.
        return Response({
            "address": address,
            "password": password,
            "imap_host": getattr(settings, "B2B_MAIL_IMAP_HOST", ""),
            "imap_port": getattr(settings, "B2B_MAIL_IMAP_PORT", 993),
            "smtp_host": getattr(settings, "B2B_MAIL_SMTP_HOST", ""),
            "smtp_port": getattr(settings, "B2B_MAIL_SMTP_PORT", 587),
            "mailbox": MailboxSerializer(repo.get_mailbox(mailbox["id"], request.user.company_id)).data,
        }, status=status.HTTP_201_CREATED)


class MailboxDetailView(WorkspaceAPIView):
    """PATCH / DELETE /api/b2b/workspace/mail/mailboxes/<id>/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _mailbox(self, request, mailbox_id: int):
        if not request.user.capabilities.get("can_manage_mailboxes"):
            return None, Response({"detail": _("Only the company owner can manage mailboxes.")},
                                  status=status.HTTP_403_FORBIDDEN)
        mailbox = repo.get_mailbox(mailbox_id, request.user.company_id)
        if mailbox is None:
            return None, Response({"detail": _("Mailbox not found.")},
                                  status=status.HTTP_404_NOT_FOUND)
        return mailbox, None

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Update a mailbox",
                         request_body=MailboxPatchSerializer,
                         responses={200: MailboxSerializer()})
    def patch(self, request, mailbox_id: int):
        mailbox, error = self._mailbox(request, mailbox_id)
        if error:
            return error

        serializer = MailboxPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        if not fields:
            return Response(MailboxSerializer(mailbox).data)

        if "is_active" in fields:
            try:
                mailcow.set_mailbox_active(mailbox["address"], fields["is_active"])
            except (mailcow.MailcowError, ImproperlyConfigured) as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        updated = repo.update_mailbox(mailbox_id, **fields)
        return Response(MailboxSerializer(
            repo.get_mailbox(updated["id"], request.user.company_id)
        ).data)

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Remove a mailbox",
                         responses={204: openapi.Response(description="Removed")})
    def delete(self, request, mailbox_id: int):
        mailbox, error = self._mailbox(request, mailbox_id)
        if error:
            return error

        try:
            mailcow.delete_mailbox(mailbox["address"])
        except (mailcow.MailcowError, ImproperlyConfigured) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # The stored mail goes with it: keeping a company's correspondence
        # after they asked for the address to be removed is not ours to decide.
        repo.update_mailbox(mailbox_id, is_active=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MailboxResetPasswordView(WorkspaceAPIView):
    """POST /api/b2b/workspace/mail/mailboxes/<id>/password/ — issue a new one."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=MAIL_TAG, operation_summary="Reset a mailbox password",
                         responses={200: MailboxCredentialSerializer()})
    def post(self, request, mailbox_id: int):
        if not request.user.capabilities.get("can_manage_mailboxes"):
            return Response({"detail": _("Only the company owner can manage mailboxes.")},
                            status=status.HTTP_403_FORBIDDEN)

        mailbox = repo.get_mailbox(mailbox_id, request.user.company_id)
        if mailbox is None:
            return Response({"detail": _("Mailbox not found.")}, status=status.HTTP_404_NOT_FOUND)

        password = crypto.generate_password()
        try:
            mailcow.set_mailbox_password(mailbox["address"], password)
        except (mailcow.MailcowError, ImproperlyConfigured) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        repo.update_mailbox(mailbox_id, smtp_password_enc=crypto.encrypt(password))
        return Response({
            "address": mailbox["address"],
            "password": password,
            "imap_host": getattr(settings, "B2B_MAIL_IMAP_HOST", ""),
            "imap_port": getattr(settings, "B2B_MAIL_IMAP_PORT", 993),
            "smtp_host": getattr(settings, "B2B_MAIL_SMTP_HOST", ""),
            "smtp_port": getattr(settings, "B2B_MAIL_SMTP_PORT", 587),
        })


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
        return Response({"updated": updated, "unread": repo.unread_notification_count(request.user.id)})
