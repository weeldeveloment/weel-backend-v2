"""Background work for corporate mail.

Everything slow or failure-prone lives here rather than in a view: talking to
an SMTP server, walking IMAP, and resolving DNS all involve a third party that
can be down, and none of them should hold a request open.
"""
from __future__ import annotations

import logging

from core.celery import app
from django.conf import settings
from django.utils import timezone

from apps.b2b.mail import crypto, dns_checks, repository as repo
from apps.b2b.mail.smtp_send import MailSendError, append_to_sent, build_message, send_message

logger = logging.getLogger(__name__)


# ─── Sending ──────────────────────────────────────────────────────────────────

@app.task(
    name="b2b.mail.send_message",
    bind=True,
    autoretry_for=(MailSendError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def send_mail_message(self, outbox_id: int) -> None:
    """Deliver one queued message.

    Retries on anything transient (server down, network); gives up immediately
    on a permanent rejection, because retrying a refused recipient for five
    rounds just delays telling the sender their address was wrong.
    """
    entry = repo.get_outbox_entry(outbox_id)
    if entry is None or entry["status"] == "sent":
        return

    mailbox = repo.get_mailbox_by_id(entry["mailbox_id"])
    if mailbox is None:
        repo.update_outbox_entry(outbox_id, status="failed", error="Mailbox no longer exists.")
        return

    payload = entry["payload"]
    repo.update_outbox_entry(outbox_id, attempts=entry["attempts"] + 1, status="sending")

    try:
        password = crypto.decrypt(mailbox["smtp_password_enc"])
        attachments = repo.list_attachments([entry["message_id"]]).get(entry["message_id"], [])

        message = build_message(
            from_address=mailbox["address"],
            from_name=mailbox.get("display_name") or mailbox.get("employee_name") or "",
            to=payload.get("to", []),
            cc=payload.get("cc", []),
            bcc=payload.get("bcc", []),
            subject=payload.get("subject", ""),
            body_text=payload.get("body_text", ""),
            body_html=payload.get("body_html", ""),
            in_reply_to=payload.get("in_reply_to"),
            references=payload.get("references"),
            attachments=attachments,
        )
        envelope = [*payload.get("to", []), *payload.get("cc", []), *payload.get("bcc", [])]
        message_id = send_message(
            address=mailbox["address"],
            password=password,
            message=message,
            envelope_recipients=envelope,
        )
    except ValueError as exc:
        # A password that will not decrypt is a configuration problem, not a
        # transient one — retrying cannot fix it.
        logger.error("Mailbox %s has an undecryptable password: %s", mailbox["address"], exc)
        _fail(outbox_id, entry["message_id"], str(exc))
        return
    except MailSendError as exc:
        if exc.permanent:
            _fail(outbox_id, entry["message_id"], str(exc))
            return
        repo.update_outbox_entry(outbox_id, status="pending", error=str(exc))
        raise

    repo.update_outbox_entry(outbox_id, status="sent", sent_at=timezone.now(), error=None)
    repo.update_message(
        entry["message_id"],
        status="sent",
        message_id_header=message_id,
        sent_at=timezone.now(),
        error=None,
    )
    message_row = repo.get_message(entry["message_id"], mailbox["id"])
    if message_row:
        repo.refresh_thread_counters(message_row["thread_id"])

    append_to_sent(address=mailbox["address"], password=password, message=message)


def _fail(outbox_id: int, message_id: int, error: str) -> None:
    repo.update_outbox_entry(outbox_id, status="failed", error=error)
    repo.update_message(message_id, status="failed", error=error)


# ─── Receiving ────────────────────────────────────────────────────────────────

@app.task(name="b2b.mail.sync_mailbox")
def sync_one_mailbox(mailbox_id: int) -> int:
    """Pull new mail for one mailbox and notify its owner. Returns the count stored."""
    from apps.b2b.mail.imap_sync import MailSyncError, sync_mailbox

    mailbox = repo.get_mailbox_by_id(mailbox_id)
    if mailbox is None or not mailbox["is_active"]:
        return 0

    try:
        stored = sync_mailbox(mailbox)
    except MailSyncError as exc:
        # Recorded on the mailbox rather than raised: the settings screen shows
        # it, and one broken mailbox should not turn into a retry storm.
        logger.warning("Mail sync failed for mailbox %s: %s", mailbox_id, exc)
        repo.update_mailbox(mailbox_id, sync_error=str(exc)[:500], last_sync_at=timezone.now())
        return 0

    for message in stored:
        _notify_new_mail(mailbox, message)
    return len(stored)


@app.task(name="b2b.mail.sync_all_mailboxes")
def sync_all_mailboxes() -> int:
    """Beat entry: fan out a sync task per active mailbox."""
    if not getattr(settings, "B2B_MAIL_ENABLED", False):
        return 0

    mailboxes = repo.list_syncable_mailboxes()
    for mailbox in mailboxes:
        sync_one_mailbox.delay(mailbox["id"])
    return len(mailboxes)


def _notify_new_mail(mailbox: dict, message: dict) -> None:
    """In-app feed row plus a push, for one arriving message."""
    sender = message.get("from_name") or message.get("from_address") or ""
    subject = message.get("subject") or "(mavzusiz)"

    repo.create_notification(
        company_id=mailbox["company_id"],
        employee_id=mailbox["employee_id"],
        kind="mail",
        title=sender,
        body=subject,
        payload={
            "thread_id": message["thread_id"],
            "message_id": message["id"],
            "mailbox_id": mailbox["id"],
        },
    )

    token = repo.get_employee_fcm_token(mailbox["employee_id"])
    if not token:
        return
    try:
        from apps.notification.service import FCMService

        FCMService.send_to_tokens(
            tokens=[token],
            title=sender,
            body=subject,
            data={
                "type": "mail",
                "thread_id": str(message["thread_id"]),
                "message_id": str(message["id"]),
            },
        )
    except Exception:  # noqa: BLE001 - the mail is already stored; push is a bonus
        logger.exception("Push notification failed for mailbox %s", mailbox["id"])


@app.task(name="b2b.mail.notify_chat_message")
def notify_chat_message(thread_id: int, sender_id: int, sender_name: str, text: str) -> int:
    """Feed row + push for a workspace chat message.

    Chat had no notification of any kind until now — a message only appeared
    when the recipient happened to open the app. It lives in this module
    because this is where the B2B feed and the FCM plumbing are.
    """
    recipients = repo.list_chat_recipients(thread_id, sender_id)
    tokens: list[str] = []

    for recipient in recipients:
        repo.create_notification(
            company_id=recipient["company_id"],
            employee_id=recipient["employee_id"],
            kind="chat",
            title=sender_name,
            body=text[:200],
            payload={"thread_id": thread_id},
        )
        if recipient.get("fcm_token"):
            tokens.append(recipient["fcm_token"])

    if tokens:
        try:
            from apps.notification.service import FCMService

            FCMService.send_to_tokens(
                tokens=tokens,
                title=sender_name,
                body=text[:200],
                data={"type": "chat", "thread_id": str(thread_id)},
            )
        except Exception:  # noqa: BLE001 - the message is already delivered
            logger.exception("Chat push failed for thread %s", thread_id)

    return len(recipients)


# ─── Domain health ────────────────────────────────────────────────────────────

@app.task(name="b2b.mail.recheck_domains")
def recheck_domains() -> int:
    """Beat entry: re-verify every domain's DNS.

    Runs against verified domains too — a customer who changes DNS provider
    silently breaks their own mail, and we would rather show them a red mark in
    the dashboard than have them discover it from a bounced invoice.
    """
    if not getattr(settings, "B2B_MAIL_ENABLED", False):
        return 0

    domains = repo.list_verifiable_domains()
    for domain in domains:
        try:
            result = dns_checks.check_domain(domain["domain"], domain["dkim_selector"])
        except Exception:  # noqa: BLE001
            logger.exception("DNS recheck failed for %s", domain["domain"])
            continue

        # MX and DKIM are the two that must hold for mail to flow in and be
        # trusted on the way out. DMARC is recommended but not a gate.
        active = result["mx_ok"] and result["dkim_ok"] and result["spf_ok"]
        repo.update_domain(
            domain["id"],
            mx_ok=result["mx_ok"],
            spf_ok=result["spf_ok"],
            dkim_ok=result["dkim_ok"],
            dmarc_ok=result["dmarc_ok"],
            status="active" if active else "pending",
            last_checked_at=timezone.now(),
            verified_at=domain.get("verified_at") or (timezone.now() if active else None),
        )
    return len(domains)
