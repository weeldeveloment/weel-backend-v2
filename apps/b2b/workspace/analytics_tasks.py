"""The morning pass that delivers report subscriptions.

One beat entry, ``b2b.workspace.report_subscriptions``, half an hour after
Weel AI's own reports. It finds every switched-on subscription whose cadence
fires today (daily every day, weekly on Monday, monthly on the 1st), builds
the section for the period that just ended, and hands it over — into the
person's own «Saqlangan xabarlar» room, by mail with the XLSX attached, or
both. One subscription per subtask, so one broken mailbox holds nobody else
up.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from core.celery import app
from django.conf import settings
from django.utils import timezone

from apps.b2b.mail import repository as mail_repo
from apps.b2b.mail.repository import create_notification
from apps.b2b.workspace import analytics, analytics_io, realtime
from apps.b2b.workspace import analytics_repository as subs
from apps.b2b.workspace import repository as wrepo
from apps.b2b.workspace.roles import is_manager

logger = logging.getLogger(__name__)

#: Pushes are Uzbek throughout the app (see `push_text`), and so is the
#: report that lands in the chat.
LANG = "uz"


@app.task(name="b2b.workspace.report_subscriptions")
def report_subscriptions() -> int:
    """Queues today's deliveries. Returns how many."""
    today = timezone.localdate()
    queued = 0
    for subscription in subs.due_subscriptions(today):
        deliver_report_subscription.delay(subscription["id"])
        queued += 1
    return queued


def period_for(frequency: str, today: date) -> tuple[str, date]:
    """The report a cadence stands for on a morning: yesterday, the week that
    ended on Sunday, the month that ended yesterday."""
    if frequency == subs.FREQUENCY_DAILY:
        return "day", today - timedelta(days=1)
    if frequency == subs.FREQUENCY_WEEKLY:
        return "week", today - timedelta(days=7)
    return "month", today.replace(day=1) - timedelta(days=1)


@app.task(name="b2b.workspace.deliver_report_subscription")
def deliver_report_subscription(subscription_id: int) -> str:
    subscription = subs.get_subscription_by_id(subscription_id)
    if not subscription or not subscription.get("is_enabled") or not subscription.get("is_active", True):
        return "skipped"

    today = timezone.localdate()
    period, anchor = period_for(subscription["frequency"], today)
    window = analytics.resolve_window(period, anchor)
    company_id = int(subscription["company_id"])
    employee_id = int(subscription["employee_id"])
    section = subscription["section"]
    # A manager's standing order is about the company; an employee's is about
    # their own work — the same rule the screen applies.
    scope = None if is_manager(subscription.get("role")) else employee_id

    try:
        report = analytics.section_report(company_id, section, window, employee_id=scope)
    except Exception as exc:  # noqa: BLE001 — recorded on the row, retried tomorrow
        logger.exception("Report subscription %s could not be built", subscription_id)
        subs.mark_delivery(subscription_id, error=f"build: {exc}", delivered=False)
        return "failed"

    text = analytics_io.summary_text(section, report, window, LANG)
    title = _title(section, window)
    errors: list[str] = []
    delivered = False

    if subs.CHANNEL_CHAT in subscription["channels"]:
        try:
            _deliver_chat(company_id, employee_id, subscription, title, text)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Report subscription %s: chat delivery failed", subscription_id)
            errors.append(f"chat: {exc}")

    if subs.CHANNEL_EMAIL in subscription["channels"]:
        try:
            _deliver_email(company_id, employee_id, subscription, section, window, scope, title, text)
            delivered = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Report subscription %s: mail delivery failed", subscription_id)
            errors.append(f"email: {exc}")

    subs.mark_delivery(subscription_id, error="; ".join(errors) or None, delivered=delivered)
    if not delivered:
        return "failed"
    return "partial" if errors else "sent"


def _title(section: str, window: analytics.Window) -> str:
    return (
        f"{analytics_io.word('report', LANG)}: {analytics_io.section_label(section, LANG)} — "
        f"{analytics_io.window_label(window, LANG)}"
    )


def _deliver_chat(company_id: int, employee_id: int, subscription: dict, title: str, text: str) -> None:
    """Into the person's own saved-messages room, live if they have the app
    open, and a push if they do not."""
    thread = wrepo.ensure_saved_thread(company_id, employee_id)
    message = wrepo.send_message(thread["id"], employee_id, text)
    if message:
        try:
            from apps.b2b.workspace.views import _message_payload

            realtime.broadcast_message(thread["id"], _message_payload(message, viewer_id=employee_id))
        except Exception:  # noqa: BLE001 — the message is stored; the socket is a convenience
            logger.exception("Report subscription: could not broadcast into thread %s", thread["id"])

    create_notification(
        company_id=company_id,
        employee_id=employee_id,
        kind="report",
        title=title,
        body=text.split("\n", 1)[-1][:200],
        payload={
            "section": subscription["section"],
            "period": subscription["frequency"],
            "thread_id": thread["id"],
        },
    )
    token = subscription.get("fcm_token")
    if not token:
        return
    try:
        from apps.b2b.workspace.repository import clear_employee_fcm_tokens, unread_badges_for_tokens
        from apps.notification.service import B2B_ANDROID_CHANNEL, FCMService, b2b_firebase_app

        FCMService.send_to_tokens(
            tokens=[token],
            title=title,
            body=text.split("\n", 1)[-1][:200],
            data={"type": "chat", "thread_id": str(thread["id"]), "kind": "report"},
            app=b2b_firebase_app(),
            android_channel_id=B2B_ANDROID_CHANNEL,
            deactivate_invalid=clear_employee_fcm_tokens,
            badge_for=unread_badges_for_tokens,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Report subscription: push failed for employee %s", employee_id)


def _deliver_email(company_id: int, employee_id: int, subscription: dict, section: str,
                   window: analytics.Window, scope: int | None, title: str, text: str) -> None:
    """With the XLSX attached, through the person's own connected inbox — or
    the deployment's SMTP host when it has one. Neither: a recorded failure,
    which the export sheet shows under the toggle."""
    from apps.b2b.workspace.analytics_views import build_export, platform_mail_configured

    recipients = [r for r in subscription.get("recipients") or [] if r]
    if not recipients and subscription.get("employee_email"):
        recipients = [subscription["employee_email"]]
    if not recipients:
        raise RuntimeError("no_recipients")

    payload, content_type, name = build_export(
        company_id, section, window, employee_id=scope, fmt="xlsx", lang=LANG
    )
    maintype, _, subtype = content_type.partition("/")

    accounts = [a for a in mail_repo.list_accounts(employee_id) if a.get("is_active", True)]
    if accounts:
        from apps.b2b.mail import smtp_send

        account = accounts[0]
        message = smtp_send.build_message(
            from_address=account["address"],
            from_name=subscription.get("full_name") or account.get("display_name") or "Weel",
            to=recipients,
            cc=[],
            bcc=[],
            subject=title,
            body_text=text,
            body_html="",
        )
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=name)
        smtp_send.send_message(account=account, message=message, envelope_recipients=recipients)
        return

    if platform_mail_configured():
        from django.core.mail import EmailMessage

        mail = EmailMessage(
            subject=title,
            body=text,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
            to=recipients,
        )
        mail.attach(name, payload, content_type)
        mail.send(fail_silently=False)
        return

    raise RuntimeError("mail_not_connected")
