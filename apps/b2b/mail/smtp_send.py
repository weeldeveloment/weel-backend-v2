"""Builds and submits an outgoing message through the sender's own provider.

We are a client of the person's existing inbox, not a mail host, so an outgoing
message is submitted to *their* provider as *them* — Gmail sends it as Gmail,
with Gmail's reputation, SPF and DKIM. There is no deliverability problem for
us to solve, which is the main practical advantage of connecting an account
over hosting one.

After a successful send the message is also ``APPEND``-ed to the account's
Sent folder. Our own database already has it, but this is somebody's real
inbox: opening the Gmail app afterwards must show the message there too, or it
will look like it was never sent.
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

from django.core.files.storage import default_storage

from apps.b2b.mail.connection import MailAuthError, open_imap, open_smtp

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
    account: dict,
    message: EmailMessage,
    envelope_recipients: list[str],
) -> str:
    """Submit through the account's own provider. Returns the Message-ID sent."""
    try:
        client = open_smtp(account)
    except MailAuthError as exc:
        raise MailSendError(str(exc), permanent=True) from exc
    except ConnectionError as exc:
        raise MailSendError(str(exc)) from exc

    try:
        client.send_message(
            message,
            from_addr=account["address"],
            to_addrs=envelope_recipients,
        )
    except smtplib.SMTPRecipientsRefused as exc:
        # Every recipient bounced at submission time — almost always a typo in
        # the address, which no amount of retrying fixes.
        raise MailSendError(f"No recipient was accepted: {exc.recipients}", permanent=True) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailSendError(f"Sender {account['address']} refused: {exc}", permanent=True) from exc
    except smtplib.SMTPDataError as exc:
        # 5xx is the provider rejecting the message itself (too large, judged
        # spammy); 4xx is a "try later".
        permanent = 500 <= exc.smtp_code < 600
        raise MailSendError(f"Message rejected: {exc}", permanent=permanent) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailSendError(f"Mail server unavailable: {exc}") from exc
    finally:
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            pass

    return message["Message-ID"]


def append_to_sent(*, account: dict, message: EmailMessage) -> None:
    """Best-effort copy into the account's Sent folder.

    Failure is logged and swallowed: the message has already gone out, and
    reporting the send as failed here would invite the user to send it twice.
    Providers also disagree on what the folder is called, so several names are
    tried before giving up.
    """
    try:
        client = open_imap(account)
    except (MailAuthError, ConnectionError):
        logger.warning("Could not open IMAP to file sent mail for %s", account["address"])
        return

    try:
        payload = message.as_bytes()
        stamp = imaplib.Time2Internaldate(time.time())
        for folder in ('"[Gmail]/Sent Mail"', "Sent", '"Sent Items"', '"&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"'):
            status, _ = client.append(folder, "\\Seen", stamp, payload)
            if status == "OK":
                return
        logger.info("No Sent folder accepted the copy for %s", account["address"])
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Could not copy sent mail for %s", account["address"], exc_info=True)
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
