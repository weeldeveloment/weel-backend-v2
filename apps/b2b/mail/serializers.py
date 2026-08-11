"""Request/response shapes for the corporate mail API.

Read serializers exist so drf-yasg can describe the endpoints — the dashboard
regenerates its TypeScript from that schema, and an endpoint documented only as
"object" produces `any` on the other side. Write serializers do the real work
of validating what a client sends.
"""
from __future__ import annotations

import re

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

# Deliberately permissive: anything with a local part, an @, and a dotted
# domain. Stricter regexes reject valid addresses more often than they catch
# typos, and the SMTP server is the real authority on deliverability.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# A domain label per RFC 1035, no leading/trailing hyphens, at least two parts.
_DOMAIN_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)

# Mailbox local part. Kept to what every mail client handles without quoting.
_LOCAL_PART_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$")


def _clean_addresses(values: list[str], field: str) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        address = (value or "").strip().lower()
        if not address:
            continue
        if not _EMAIL_RE.match(address):
            raise serializers.ValidationError({field: [_("«%(value)s» is not a valid email address.") % {"value": address}]})
        if address not in cleaned:
            cleaned.append(address)
    return cleaned


# ─── Domains ──────────────────────────────────────────────────────────────────

class MailDnsRecordSerializer(serializers.Serializer):
    kind = serializers.CharField()
    host = serializers.CharField()
    value = serializers.CharField()
    priority = serializers.IntegerField(required=False)
    note = serializers.CharField(required=False)


class MailDomainSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    domain = serializers.CharField()
    status = serializers.CharField()
    dkim_selector = serializers.CharField()
    mx_ok = serializers.BooleanField()
    spf_ok = serializers.BooleanField()
    dkim_ok = serializers.BooleanField()
    dmarc_ok = serializers.BooleanField()
    last_error = serializers.CharField(allow_null=True, required=False)
    last_checked_at = serializers.DateTimeField(allow_null=True, required=False)
    verified_at = serializers.DateTimeField(allow_null=True, required=False)
    dns_records = MailDnsRecordSerializer(many=True, required=False)


class MailDomainCreateSerializer(serializers.Serializer):
    domain = serializers.CharField(max_length=253)

    def validate_domain(self, value: str) -> str:
        domain = (value or "").strip().lower().rstrip(".")
        # People paste what they see in a browser; strip it back to the name.
        domain = re.sub(r"^https?://", "", domain).split("/")[0].strip()
        if domain.startswith("www."):
            domain = domain[4:]
        if not _DOMAIN_RE.match(domain):
            raise serializers.ValidationError(
                _("Enter a domain like «kompaniya.com», without http:// or a path.")
            )
        return domain


# ─── Mailboxes ────────────────────────────────────────────────────────────────

class MailboxSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    address = serializers.CharField()
    local_part = serializers.CharField()
    display_name = serializers.CharField(allow_null=True, required=False)
    employee_id = serializers.IntegerField()
    employee_name = serializers.CharField(required=False)
    domain_name = serializers.CharField(required=False)
    domain_status = serializers.CharField(required=False)
    quota_bytes = serializers.IntegerField()
    daily_send_limit = serializers.IntegerField()
    is_active = serializers.BooleanField()
    last_sync_at = serializers.DateTimeField(allow_null=True, required=False)
    sync_error = serializers.CharField(allow_null=True, required=False)


class MailboxCreateSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField()
    domain_id = serializers.IntegerField()
    local_part = serializers.CharField(max_length=64)
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_local_part(self, value: str) -> str:
        local_part = (value or "").strip().lower()
        if not _LOCAL_PART_RE.match(local_part):
            raise serializers.ValidationError(
                _("Use latin letters, digits, dot, dash or underscore — for example «aziz.karimov».")
            )
        # Addresses every domain is expected to answer on, or that would let
        # someone impersonate the company's own automated mail.
        if local_part in {"postmaster", "abuse", "hostmaster", "webmaster", "noreply", "no-reply", "mailer-daemon"}:
            raise serializers.ValidationError(_("This address is reserved."))
        return local_part


class MailboxPatchSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    daily_send_limit = serializers.IntegerField(required=False, min_value=1, max_value=5000)


class MailboxCredentialSerializer(serializers.Serializer):
    """The one and only time a mailbox password is ever returned."""

    address = serializers.CharField()
    password = serializers.CharField()
    imap_host = serializers.CharField()
    imap_port = serializers.IntegerField()
    smtp_host = serializers.CharField()
    smtp_port = serializers.IntegerField()


# ─── Threads & messages ───────────────────────────────────────────────────────

class MailRecipientSerializer(serializers.Serializer):
    kind = serializers.CharField()
    address = serializers.CharField()
    name = serializers.CharField(allow_blank=True)


class MailAttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    size_bytes = serializers.IntegerField()
    download_url = serializers.CharField(required=False)


class MailThreadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    subject = serializers.CharField()
    snippet = serializers.CharField()
    folder = serializers.CharField()
    participants = serializers.CharField()
    message_count = serializers.IntegerField()
    unread_count = serializers.IntegerField()
    is_starred = serializers.BooleanField()
    last_message_at = serializers.DateTimeField(allow_null=True)


class MailMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    thread_id = serializers.IntegerField()
    direction = serializers.CharField()
    status = serializers.CharField()
    from_address = serializers.CharField()
    from_name = serializers.CharField(allow_blank=True)
    subject = serializers.CharField(allow_blank=True)
    body_text = serializers.CharField(allow_blank=True)
    body_html = serializers.CharField(allow_blank=True)
    has_attachments = serializers.BooleanField()
    is_read = serializers.BooleanField()
    error = serializers.CharField(allow_null=True, required=False)
    sent_at = serializers.DateTimeField(allow_null=True)
    recipients = MailRecipientSerializer(many=True, required=False)
    attachments = MailAttachmentSerializer(many=True, required=False)


class MailSendSerializer(serializers.Serializer):
    to = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    cc = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    bcc = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    subject = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")
    body_text = serializers.CharField(allow_blank=True, required=False, default="")
    body_html = serializers.CharField(allow_blank=True, required=False, default="")
    reply_to_message_id = serializers.IntegerField(required=False, allow_null=True)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )

    def validate(self, attrs):
        attrs["to"] = _clean_addresses(attrs.get("to", []), "to")
        attrs["cc"] = _clean_addresses(attrs.get("cc", []), "cc")
        attrs["bcc"] = _clean_addresses(attrs.get("bcc", []), "bcc")

        if not attrs["to"]:
            raise serializers.ValidationError({"to": [_("Add at least one recipient.")]})

        total = len(attrs["to"]) + len(attrs["cc"]) + len(attrs["bcc"])
        max_recipients = getattr(settings, "B2B_MAIL_MAX_RECIPIENTS", 25)
        if total > max_recipients:
            # A single message to hundreds of addresses is what mailing-list
            # software is for; through a personal mailbox it reads as spam to
            # the receiving side and costs the sending IP its reputation.
            raise serializers.ValidationError({
                "to": [_("A message may not have more than %(n)d recipients.") % {"n": max_recipients}]
            })

        if not (attrs.get("body_text") or attrs.get("body_html")):
            raise serializers.ValidationError({"body_text": [_("The message is empty.")]})
        return attrs


class MailThreadFlagsSerializer(serializers.Serializer):
    is_starred = serializers.BooleanField(required=False)
    folder = serializers.ChoiceField(
        choices=["inbox", "archive", "trash"], required=False
    )


# ─── Notifications ────────────────────────────────────────────────────────────

class B2BNotificationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField(allow_blank=True)
    payload = serializers.JSONField()
    is_read = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class NotificationReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
