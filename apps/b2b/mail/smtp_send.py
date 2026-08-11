"""Builds and submits an outgoing message.

The message is submitted authenticating **as the sending mailbox**, not through
a shared application account. That is what makes the envelope sender, the
``From`` header and the DKIM signature all agree on ``aziz@kompaniya.com`` —
alignment is what Gmail actually checks, and a shared relay account would fail
it for every company at once.

After a successful send the message is also ``APPEND``-ed to the mailbox's
Sent folder. Our own database already has it, but a mailbox is a real mailbox:
somebody reading it from the Gmail app must see their sent mail there too.
"""
from __future__ import annotations

import imaplib
import logging
import mimetypes
import smtplib
import time
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class MailSendError(RuntimeError):
    """Sending failed. Retryable unless ``permanent`` is set."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def build_message(
    *,
    from_address: str,
    from_name: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    body_html: str,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((str(Header(from_name, "utf-8")), from_address))
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    # Bcc is deliberately not written as a header — it goes only into the
    # envelope at `send_message` time. A Bcc header would be delivered to every
    # recipient, which is the exact opposite of what the sender asked for.
    message["Subject"] = str(Header(subject, "utf-8"))
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=from_address.split("@")[-1])

    # Threading headers: without these, a reply shows up in the recipient's
    # client as a brand-new conversation.
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to

    message.set_content(body_text or " ")
    if body_html:
        message.add_alternative(body_html, subtype="html")

    for attachment in attachments or []:
        _attach(message, attachment)

    return message


def _attach(message: EmailMessage, attachment: dict) -> None:
    storage_key = attachment["storage_key"]
    try:
        with default_storage.open(storage_key, "rb") as handle:
            payload = handle.read()
    except (OSError, ValueError) as exc:
        # A missing file must not silently send a message without the document
        # the sender attached — they would never know it went out incomplete.
        raise MailSendError(
            f"Attachment {attachment.get('filename')!r} could not be read.",
            permanent=True,
        ) from exc

    content_type = attachment.get("content_type") or ""
    if "/" not in content_type:
        content_type = mimetypes.guess_type(attachment["filename"])[0] or "application/octet-stream"
    maintype, _, subtype = content_type.partition("/")

    message.add_attachment(
        payload,
        maintype=maintype,
        subtype=subtype or "octet-stream",
        filename=attachment["filename"],
    )


def send_message(
    *,
    address: str,
    password: str,
    message: EmailMessage,
    envelope_recipients: list[str],
) -> str:
    """Submit over SMTP. Returns the Message-ID that went out."""
    host = getattr(settings, "B2B_MAIL_SMTP_HOST", "")
    port = getattr(settings, "B2B_MAIL_SMTP_PORT", 587)
    if not host:
        raise MailSendError("B2B_MAIL_SMTP_HOST is not configured.", permanent=True)

    try:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls()
            client.ehlo()
            client.login(address, password)
            client.send_message(message, from_addr=address, to_addrs=envelope_recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailSendError(
            f"Mailbox {address} was rejected by the mail server. Its password "
            f"may need resetting: {exc}",
            permanent=True,
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        # Every recipient bounced at submission time — almost always a typo in
        # the address, which no amount of retrying fixes.
        raise MailSendError(f"No recipient was accepted: {exc.recipients}", permanent=True) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailSendError(f"Sender {address} refused: {exc}", permanent=True) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailSendError(f"Mail server unavailable: {exc}") from exc

    return message["Message-ID"]


def append_to_sent(*, address: str, password: str, message: EmailMessage) -> None:
    """Best-effort copy into the mailbox's Sent folder.

    Failure is logged and swallowed: the message has already been delivered to
    the outside world, and reporting the send as failed here would invite the
    user to send it a second time.
    """
    host = getattr(settings, "B2B_MAIL_IMAP_HOST", "") or getattr(settings, "B2B_MAIL_SMTP_HOST", "")
    port = getattr(settings, "B2B_MAIL_IMAP_PORT", 993)
    if not host:
        return

    try:
        with imaplib.IMAP4_SSL(host, port, timeout=30) as client:
            client.login(address, password)
            client.append("Sent", "\\Seen", imaplib.Time2Internaldate(time.time()),
                          message.as_bytes())
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Could not copy sent mail into %s's Sent folder", address, exc_info=True)
