"""Pulls new mail out of a connected inbox and into ``b2b_mail_*``.

Neither client speaks IMAP — the dashboard is a browser and the phone would
have to hold the account's credential — so this is what makes an arriving
message visible in the chat section.

Sync is incremental and idempotent, on two independent guards:

* ``last_seen_uid`` — IMAP UIDs only ever increase within a mailbox, so
  ``UID SEARCH UID <last+1>:*`` asks for exactly the messages we have not
  looked at. ``UIDVALIDITY`` is checked alongside it, because a mailbox the
  provider rebuilds resets its UID sequence, and the stored watermark then
  points at the wrong messages.
* ``message_id_header`` — unique per account in the database, so a message that
  slips through the first guard twice still cannot be stored twice.

Both matter: the first keeps the sync cheap, the second keeps it correct.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import uuid
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.b2b.mail import repository as repo
from apps.b2b.mail.connection import MailAuthError, open_imap
from apps.b2b.mail.sanitize import html_to_text, make_snippet, sanitize_html

logger = logging.getLogger(__name__)


class MailSyncError(RuntimeError):
    pass


def _decode(value: str | None) -> str:
    """Decode an RFC 2047 header (`=?utf-8?B?...?=`) into plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        # Malformed encodings are common in spam; the raw value is still more
        # useful to a reader than dropping the header entirely.
        return value.strip()


def _addresses(message: Message, header: str) -> list[tuple[str, str]]:
    raw = message.get_all(header, [])
    if not raw:
        return []
    out = []
    for name, address in getaddresses([_decode(value) for value in raw]):
        if address and "@" in address:
            out.append((_decode(name), address.strip().lower()))
    return out


def _reference_ids(message: Message) -> list[str]:
    ids: list[str] = []
    for header in ("References", "In-Reply-To"):
        value = message.get(header)
        if value:
            ids.extend(re.findall(r"<[^>]+>", value))
    # De-duplicate but keep order — the last reference is the direct parent and
    # the most likely match.
    seen: set[str] = set()
    return [ref for ref in reversed(ids) if not (ref in seen or seen.add(ref))]


def _bodies(message: Message) -> tuple[str, str, list[dict]]:
    """Split a parsed message into (text, html, attachments)."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict] = []

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue

        disposition = (part.get_content_disposition() or "").lower()
        filename = _decode(part.get_filename())
        content_type = part.get_content_type()

        if disposition == "attachment" or (filename and content_type not in ("text/plain", "text/html")):
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            attachments.append({
                "filename": filename or f"attachment-{len(attachments) + 1}",
                "content_type": content_type,
                "payload": payload,
                "content_id": (part.get("Content-ID") or "").strip("<>") or None,
                "is_inline": disposition == "inline",
            })
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")

        if content_type == "text/html":
            html_parts.append(decoded)
        elif content_type == "text/plain":
            text_parts.append(decoded)

    return "\n".join(text_parts).strip(), "\n".join(html_parts).strip(), attachments


def _store_attachment(account_id: int, message_row_id: int, attachment: dict) -> None:
    max_mb = getattr(settings, "B2B_MAIL_MAX_ATTACHMENT_MB", 20)
    payload = attachment["payload"]
    if len(payload) > max_mb * 1024 * 1024:
        logger.info(
            "Skipping oversized attachment %s (%d bytes) on message %s",
            attachment["filename"], len(payload), message_row_id,
        )
        return

    # The filename came from the sender, so it never becomes part of the path.
    safe_name = re.sub(r"[^\w.\-]", "_", attachment["filename"])[:120] or "file"
    storage_key = f"b2b/mail/{account_id}/{uuid.uuid4().hex}/{safe_name}"
    default_storage.save(storage_key, ContentFile(payload))

    repo.create_attachment(
        account_id=account_id,
        message_id=message_row_id,
        filename=attachment["filename"],
        content_type=attachment["content_type"],
        size_bytes=len(payload),
        storage_key=storage_key,
        content_id=attachment["content_id"],
        is_inline=attachment["is_inline"],
    )


def store_message(account: dict, raw: bytes, *, imap_uid: int | None = None) -> dict | None:
    """Parse one RFC 822 message and persist it. Returns the stored row, or None if a duplicate."""
    message = email.message_from_bytes(raw)

    message_id_header = (message.get("Message-ID") or "").strip() or None
    if message_id_header and repo.message_exists(account["id"], message_id_header):
        return None

    subject = _decode(message.get("Subject"))
    senders = _addresses(message, "From")
    from_name, from_address = senders[0] if senders else ("", "")

    body_text, body_html, attachments = _bodies(message)
    sanitized_html = sanitize_html(body_html) if body_html else ""
    if not body_text and sanitized_html:
        body_text = html_to_text(sanitized_html)

    try:
        sent_at = parsedate_to_datetime(message.get("Date")) if message.get("Date") else None
    except (TypeError, ValueError):
        sent_at = None
    if sent_at and timezone.is_naive(sent_at):
        sent_at = timezone.make_aware(sent_at, timezone.utc)

    references = _reference_ids(message)
    thread = repo.find_thread_for_message(
        account["id"],
        folder="inbox",
        references=references,
        subject_key=repo.normalize_subject(subject),
    )
    if thread is None:
        participants = ", ".join(
            filter(None, [from_address, *(addr for _, addr in _addresses(message, "To"))])
        )
        thread = repo.create_thread(
            account_id=account["id"],
            subject=subject or "(mavzusiz)",
            folder="inbox",
            participants=participants,
            snippet=make_snippet(body_text),
            last_message_at=sent_at or timezone.now(),
        )
    if thread is None:
        raise MailSyncError("Could not create a thread for the incoming message.")

    stored = repo.create_message(
        thread_id=thread["id"],
        account_id=account["id"],
        direction="inbound",
        status="delivered",
        imap_uid=imap_uid,
        message_id_header=message_id_header,
        in_reply_to=(message.get("In-Reply-To") or "").strip() or None,
        references_header=(message.get("References") or "").strip() or None,
        from_address=from_address,
        from_name=from_name,
        subject=subject,
        body_text=body_text,
        body_html_sanitized=sanitized_html,
        has_attachments=any(not a["is_inline"] for a in attachments),
        is_read=False,
        sent_at=sent_at,
    )
    if stored is None:
        return None

    recipients = [
        (kind, address, name)
        for kind, header in (("to", "To"), ("cc", "Cc"))
        for name, address in _addresses(message, header)
    ]
    repo.add_recipients(stored["id"], recipients)

    for attachment in attachments:
        try:
            _store_attachment(account["id"], stored["id"], attachment)
        except Exception:  # noqa: BLE001 - one bad attachment must not lose the message
            logger.exception("Failed to store attachment on message %s", stored["id"])

    repo.refresh_thread_counters(thread["id"])
    stored["thread_id"] = thread["id"]
    return stored


def sync_account(account: dict) -> list[dict]:
    """Fetch everything new in one inbox. Returns the messages that were stored."""
    batch = getattr(settings, "B2B_MAIL_SYNC_BATCH", 50)
    stored: list[dict] = []
    client = None

    try:
        # Authentication — app password or Google token — is settled in
        # `connection`, so nothing below branches on how this account was
        # connected.
        client = open_imap(account)
        try:
            status, data = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailSyncError(f"Could not open INBOX for {account['address']}.")

            uid_validity = _uid_validity(client)
            last_seen = int(account.get("last_seen_uid") or 0)
            stored_validity = account.get("uid_validity")

            # The provider rebuilt the mailbox: UIDs restart, so our watermark
            # now points into a different sequence and would skip real mail.
            # Start over — the Message-ID guard stops that re-importing
            # duplicates.
            if stored_validity is not None and uid_validity != stored_validity:
                logger.warning(
                    "UIDVALIDITY changed for %s (%s → %s); resyncing from the start",
                    account["address"], stored_validity, uid_validity,
                )
                last_seen = 0

            status, data = client.uid("SEARCH", None, f"UID {last_seen + 1}:*")
            if status != "OK":
                raise MailSyncError(f"UID SEARCH failed for {account['address']}.")

            # `<last+1>:*` always returns at least the newest message even when
            # nothing is new, because `*` is clamped to the highest existing
            # UID. Filtering here is what stops re-storing it every minute.
            uids = [int(uid) for uid in data[0].split() if int(uid) > last_seen]
            highest = last_seen

            for uid in sorted(uids)[:batch]:
                status, payload = client.uid("FETCH", str(uid), "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    logger.warning("Could not fetch UID %s for %s", uid, account["address"])
                    continue
                try:
                    message = store_message(account, payload[0][1], imap_uid=uid)
                except Exception:  # noqa: BLE001 - a single unparseable message must not
                    # stall the account forever; the watermark still advances.
                    logger.exception("Failed to store UID %s for %s", uid, account["address"])
                else:
                    if message:
                        stored.append(message)
                highest = max(highest, uid)
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    except MailAuthError:
        # The credential stopped working. Distinct from a transient failure:
        # the caller deactivates the account and asks the person to reconnect,
        # rather than retrying a password that has been revoked.
        raise
    except imaplib.IMAP4.error as exc:
        raise MailSyncError(f"IMAP error for {account['address']}: {exc}") from exc
    except (OSError, ConnectionError) as exc:
        raise MailSyncError(f"Mail server unreachable: {exc}") from exc

    repo.update_account(
        account["id"],
        last_seen_uid=highest,
        uid_validity=uid_validity,
        last_sync_at=timezone.now(),
        sync_error=None,
    )
    return stored


def _uid_validity(client: imaplib.IMAP4_SSL) -> int | None:
    status, data = client.status("INBOX", "(UIDVALIDITY)")
    if status != "OK" or not data:
        return None
    match = re.search(rb"UIDVALIDITY\s+(\d+)", data[0])
    return int(match.group(1)) if match else None
