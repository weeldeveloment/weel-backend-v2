"""Background work for connected mail accounts.

Everything slow or failure-prone lives here rather than in a view: every one of
these talks to somebody else's mail provider, which can be slow, rate-limited
or down, and none of them should hold a request open.
"""
from __future__ import annotations

import logging

from core.celery import app
from django.conf import settings
from django.utils import timezone

from apps.b2b.mail import repository as repo
from apps.b2b.mail.connection import MailAuthError
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
    """Deliver one queued message through its account's provider.

    Retries on anything transient (provider down, rate limited); gives up
    immediately on a permanent rejection, because retrying a refused recipient
    for five rounds only delays telling the sender their address was wrong.
    """
    entry = repo.get_outbox_entry(outbox_id)
    if entry is None or entry["status"] == "sent":
        return

    account = repo.get_account_by_id(entry["account_id"])
    if account is None:
        repo.update_outbox_entry(outbox_id, status="failed", error="Account was disconnected.")
        return

    payload = entry["payload"]
    repo.update_outbox_entry(outbox_id, attempts=entry["attempts"] + 1, status="sending")

    try:
        attachments = repo.list_attachments([entry["message_id"]]).get(entry["message_id"], [])
        message = build_message(
            from_address=account["address"],
            from_name=account.get("display_name") or account.get("employee_name") or "",
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
            account=account,
            message=message,
            envelope_recipients=envelope,
        )
    except MailAuthError as exc:
        # The credential stopped working. Deactivating the account is what
        # surfaces the "reconnect" prompt in both apps — retrying a revoked
        # app password would just fail five more times in silence.
        logger.warning("Account %s can no longer authenticate: %s", account["address"], exc)
        repo.update_account(account["id"], is_active=False, sync_error=str(exc)[:500])
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
    message_row = repo.get_message(entry["message_id"], account["id"])
    if message_row:
        repo.refresh_thread_counters(message_row["thread_id"])

    append_to_sent(account=account, message=message)


def _fail(outbox_id: int, message_id: int, error: str) -> None:
    repo.update_outbox_entry(outbox_id, status="failed", error=error)
    repo.update_message(message_id, status="failed", error=error)


# ─── Receiving ────────────────────────────────────────────────────────────────

@app.task(name="b2b.mail.sync_account")
def sync_one_account(account_id: int) -> int:
    """Pull new mail for one account and notify its owner. Returns the count stored."""
    from apps.b2b.mail.imap_sync import MailSyncError, sync_account

    account = repo.get_account_by_id(account_id)
    if account is None or not account["is_active"]:
        return 0

    try:
        stored = sync_account(account)
    except MailAuthError as exc:
        # Permanent: the app password was revoked or the Google grant removed.
        # Deactivating is what makes both apps show "reconnect" instead of
        # quietly serving stale mail forever.
        logger.info("Deactivating %s — credential no longer valid: %s", account["address"], exc)
        repo.update_account(
            account_id,
            is_active=False,
            sync_error=str(exc)[:500],
            last_sync_at=timezone.now(),
        )
        return 0
    except MailSyncError as exc:
        # Transient. Recorded on the account so the screen can say so, but not
        # raised — one unreachable provider should not become a retry storm.
        logger.warning("Mail sync failed for account %s: %s", account_id, exc)
        repo.update_account(account_id, sync_error=str(exc)[:500], last_sync_at=timezone.now())
        return 0

    for message in stored:
        _notify_new_mail(account, message)
    return len(stored)


@app.task(name="b2b.mail.sync_all_accounts")
def sync_all_accounts() -> int:
    """Beat entry: fan out a sync task per connected account."""
    if not getattr(settings, "B2B_MAIL_ENABLED", False):
        return 0

    accounts = repo.list_syncable_accounts()
    for account in accounts:
        sync_one_account.delay(account["id"])
    return len(accounts)


def _notify_new_mail(account: dict, message: dict) -> None:
    """In-app feed row plus a push, for one arriving message."""
    sender = message.get("from_name") or message.get("from_address") or ""
    subject = message.get("subject") or "(mavzusiz)"

    repo.create_notification(
        company_id=account["company_id"],
        employee_id=account["employee_id"],
        kind="mail",
        title=sender,
        body=subject,
        payload={
            "thread_id": message["thread_id"],
            "message_id": message["id"],
            "account_id": account["id"],
        },
    )

    token = repo.get_employee_fcm_token(account["employee_id"])
    if not token:
        return
    try:
        from apps.b2b.workspace.repository import clear_employee_fcm_tokens
        from apps.notification.service import (
            B2B_ANDROID_CHANNEL,
            FCMService,
            b2b_firebase_app,
        )

        FCMService.send_to_tokens(
            tokens=[token],
            title=sender,
            body=subject,
            data={
                "type": "mail",
                "thread_id": str(message["thread_id"]),
                "message_id": str(message["id"]),
            },
            # The workspace app lives in its own Firebase project, so its
            # tokens are only addressable from that project's app.
            app=b2b_firebase_app(),
            # And it is posted to the channel that app creates: the workspace
            # app is the only one of the three that creates any.
            android_channel_id=B2B_ANDROID_CHANNEL,
            # And a dead one has to be cleared from `b2b_employee`, not from
            # the consumer table the default cleanup knows about.
            deactivate_invalid=clear_employee_fcm_tokens,
        )
    except Exception:  # noqa: BLE001 - the mail is already stored; push is a bonus
        logger.exception("Push notification failed for account %s", account["id"])


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
            from apps.b2b.workspace.repository import clear_employee_fcm_tokens
            from apps.notification.service import (
                B2B_ANDROID_CHANNEL,
                FCMService,
                b2b_firebase_app,
            )

            FCMService.send_to_tokens(
                tokens=tokens,
                title=sender_name,
                body=text[:200],
                data={"type": "chat", "thread_id": str(thread_id)},
                app=b2b_firebase_app(),
                android_channel_id=B2B_ANDROID_CHANNEL,
                deactivate_invalid=clear_employee_fcm_tokens,
            )
        except Exception:  # noqa: BLE001 - the message is already delivered
            logger.exception("Chat push failed for thread %s", thread_id)

    return len(recipients)
