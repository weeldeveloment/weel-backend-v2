"""Request/response shapes for the mail API.

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
# typos, and the provider is the real authority on deliverability.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_addresses(values: list[str], field: str) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        address = (value or "").strip().lower()
        if not address:
            continue
        if not _EMAIL_RE.match(address):
            raise serializers.ValidationError({
                field: [_("«%(value)s» is not a valid email address.") % {"value": address}]
            })
        if address not in cleaned:
            cleaned.append(address)
    return cleaned


# ─── Connected accounts ───────────────────────────────────────────────────────

class MailAccountSerializer(serializers.Serializer):
    """One connected inbox. The credential is never part of this."""

    id = serializers.IntegerField()
    address = serializers.CharField()
    display_name = serializers.CharField(allow_null=True, required=False)
    provider = serializers.CharField()
    auth_type = serializers.CharField()
    imap_host = serializers.CharField()
    smtp_host = serializers.CharField()
    is_active = serializers.BooleanField()
    last_sync_at = serializers.DateTimeField(allow_null=True, required=False)
    # Set when the provider stopped accepting us. The apps read it to show
    # "reconnect" rather than an empty inbox.
    sync_error = serializers.CharField(allow_null=True, required=False)
    unread = serializers.IntegerField(required=False)


class MailProviderHintSerializer(serializers.Serializer):
    """What the connect screen shows once an address has been typed."""

    provider = serializers.CharField()
    label = serializers.CharField()
    imap_host = serializers.CharField()
    imap_port = serializers.IntegerField()
    smtp_host = serializers.CharField()
    smtp_port = serializers.IntegerField()
    requires_app_password = serializers.BooleanField()
    help_url = serializers.CharField(allow_null=True, required=False)
    supports_oauth = serializers.BooleanField()


class MailAccountConnectSerializer(serializers.Serializer):
    """Connecting an inbox with an app password.

    The server settings are optional: they are guessed from the address for
    every provider we know, and only a self-hosted or unusual domain needs
    them typed in.
    """

    address = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    imap_host = serializers.CharField(max_length=253, required=False, allow_blank=True)
    imap_port = serializers.IntegerField(required=False, min_value=1, max_value=65535)
    smtp_host = serializers.CharField(max_length=253, required=False, allow_blank=True)
    smtp_port = serializers.IntegerField(required=False, min_value=1, max_value=65535)

    def validate_address(self, value: str) -> str:
        return value.strip().lower()

    def validate_password(self, value: str) -> str:
        # App passwords are shown grouped in fours ("abcd efgh ijkl mnop") and
        # people paste them that way; the spaces are presentation, not part of
        # the secret, and leaving them in makes a correct password fail.
        return value.replace(" ", "").strip()


class MailAccountPatchSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True)


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
    account_id = serializers.IntegerField()
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
    account_id = serializers.IntegerField(required=False, allow_null=True)
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
            # software is for. Sent through somebody's personal inbox it reads
            # as spam to the receiving side and risks their own account.
            raise serializers.ValidationError({
                "to": [_("A message may not have more than %(n)d recipients.")
                       % {"n": max_recipients}]
            })

        if not (attrs.get("body_text") or attrs.get("body_html")):
            raise serializers.ValidationError({"body_text": [_("The message is empty.")]})
        return attrs


class MailThreadFlagsSerializer(serializers.Serializer):
    is_starred = serializers.BooleanField(required=False)
    folder = serializers.ChoiceField(choices=["inbox", "archive", "trash"], required=False)


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
