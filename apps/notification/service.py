import logging

from django.conf import settings
from firebase_admin import messaging

from shared.raw.db import execute, fetch_all
from .raw_repository import create_notification


logger = logging.getLogger(__name__)


class B2BFirebaseNotConfigured(RuntimeError):
    """Raised when a B2B push is attempted with no B2B Firebase project wired up."""


def b2b_firebase_app():
    """The Firebase app a push to a B2B workspace employee must be sent from.

    The workspace app is registered in its own Firebase project, and an FCM
    token can only be addressed by the project that issued it — sending a B2B
    token from the consumer project fails with `SenderId mismatch`.

    Raises rather than falling back to the default app. The fallback used to be
    the safe-looking option, but there is no deployment where it does anything
    useful: every token this app addresses was issued by the B2B project, so
    the default app cannot deliver a single one of them. What it did instead
    was turn a missing credential into a per-token `SenderId mismatch` buried
    in the send loop, which reads like a token problem and hides the one thing
    that is actually wrong — see the deployment note below.

    The three B2B senders all call this inside a `try` that logs and carries
    on, so a misconfigured deployment loses its pushes exactly as before; the
    difference is that the log now names the cause.

    Deployment: set `FIREBASE_B2B_CREDENTIALS_JSON` to the service-account JSON
    itself. `FIREBASE_B2B_CREDENTIALS_FILE` works locally, but `certificates/`
    is in both `.gitignore` and `.dockerignore` — the path it names does not
    exist inside the image.
    """
    app = getattr(settings, "FIREBASE_B2B_APP", None)
    if app is None:
        raise B2BFirebaseNotConfigured(
            "No B2B Firebase project is configured, so B2B push cannot be sent: "
            "the workspace app's FCM tokens are only addressable from the "
            "project that issued them. Set FIREBASE_B2B_CREDENTIALS_JSON to the "
            "B2B service-account JSON (a *_CREDENTIALS_FILE path is not enough "
            "in the container — certificates/ is excluded from the image)."
        )
    return app


def _mask_token(token: str | None) -> str:
    if not token:
        return "unknown"
    if len(token) <= 12:
        return token
    return f"{token[:8]}...{token[-4:]}"


# The workspace app's notification channel: the one `PushService` creates at
# startup and the one its manifest names as `default_notification_channel_id`.
# Named here as well because the manifest default only applies to a message
# that carries no channel of its own, and because it is the *only* channel that
# may be sent — the consumer and partner apps create none, and a notification
# addressed to a channel that does not exist on the device is dropped by
# Android outright rather than falling back to a default.
B2B_ANDROID_CHANNEL = "weel_workspace"


def _android_config(
    channel_id: str | None, badge: int | None = None
) -> messaging.AndroidConfig:
    """Delivery hints for the Android half of a push.

    `high` priority is what wakes a dozing device to deliver immediately;
    without it a push can sit until the next maintenance window, which on a
    phone that has been idle in a pocket is the difference between "instantly"
    and "twenty minutes later, if at all".

    The channel is only named when the receiving app is known to have created
    it — see [B2B_ANDROID_CHANNEL]. Left out, Android uses whatever the app's
    manifest declares as its default.

    `badge` is the number the launcher may show on the app icon. Android has
    no icon badge of its own: launchers that draw one (Samsung, Pixel, MIUI)
    count the app's notifications in the shade, and `notification_count` is
    what a launcher that shows a number reads instead of counting to one per
    notification. Only sent when a badge is known — see `send_to_tokens`.
    """
    notification = (
        messaging.AndroidNotification(
            channel_id=channel_id,
            sound="default",
            default_vibrate_timings=True,
            notification_count=badge,
        )
        if channel_id
        else None
    )
    return messaging.AndroidConfig(priority="high", notification=notification)


def _apns_config(
    title: str, body: str, badge: int | None = None
) -> messaging.APNSConfig:
    """The APNs half of a push, which FCM does not fill in on its own.

    Without it iOS receives an alert with no `sound` key, and an alert with no
    sound is delivered quietly: no banner while the phone is in use, and on
    iOS 15 and up it is eligible to be held back for the scheduled notification
    summary instead of being shown when it arrives. From the outside that is
    indistinguishable from a push that never arrived at all.

    `apns-priority: 10` is what asks for immediate delivery. The alert is
    repeated in the payload because an `aps` dictionary given explicitly
    replaces the one FCM would otherwise have written, rather than merging
    into it.

    `badge` is the number iOS puts on the app icon. It is absolute, not
    additive — the phone shows exactly what the last push said — so the
    sender has to know the recipient's whole unread count, not just that one
    more thing arrived. `None` leaves the icon as it is.
    """
    return messaging.APNSConfig(
        headers={"apns-priority": "10"},
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=title, body=body),
                badge=badge,
                sound="default",
                mutable_content=True,
            ),
        ),
    )


def _badges_for(tokens: list[str], badge_for) -> dict[str, int] | None:
    """The icon-badge number for each token, or None to send without any.

    Never fatal: a badge is decoration on a push that already has a reason
    to exist, and a counter that could not be read must not cost the message.
    """
    if badge_for is None:
        return None
    try:
        badges = badge_for(tokens) or {}
    except Exception:
        logger.exception("Push badge counts could not be read; sending without badges.")
        return None
    return {
        token: int(count) for token, count in badges.items() if count is not None
    }


class FCMService:
    @staticmethod
    def _deactivate_invalid_tokens(tokens: list[str]):
        if not tokens:
            return
        execute(
            """
            UPDATE public.users
            SET fcm_token = NULL,
                device_type = NULL,
                updated_at = NOW()
            WHERE fcm_token = ANY(%s)
            """,
            [tokens],
        )

    @staticmethod
    def send_to_tokens(
        tokens: list[str],
        title: str,
        body: str,
        data: dict | None = None,
        app=None,
        deactivate_invalid=None,
        android_channel_id=None,
        badge_for=None,
    ):
        """Send one message to many tokens.

        `app` names the Firebase app to send from, and `None` means the default
        one — which is what every consumer and partner send passes. B2B callers
        hand in `b2b_firebase_app()` instead, because their tokens come from a
        different Firebase project and are not addressable from this one.

        `android_channel_id` names the Android notification channel the push
        should be posted to, and defaults to none — meaning "whatever the
        receiving app's manifest calls its default". Only the B2B senders pass
        one, because only the workspace app creates a channel; naming it for an
        app that has not created it would have Android drop the notification
        instead of showing it.

        `deactivate_invalid` is what clears the tokens Firebase reports as dead,
        and it exists for the same reason `app` does: a consumer token lives in
        `public.users`, a workspace one in `b2b_employee`, and the default —
        [_deactivate_invalid_tokens] — only knows the first. Left unset, every
        consumer and partner send behaves exactly as it always has; B2B callers
        pass their own so a dead workspace token is actually cleared instead of
        being retried on every message forever.

        `badge_for` turns the token list into `{token: unread count}` — the
        number each phone should show on the app icon after this push. With
        it, every token gets a message of its own, because iOS badges are
        absolute and two people rarely have the same count; without it the
        whole list goes as one multicast, as before. The B2B senders pass
        `repository.unread_badges_for_tokens`, which counts the feed rows they
        have just written — so the badge is exactly "how many notifications
        have arrived since you last opened the app".
        """
        normalized_data = data or {}
        if not tokens:
            logger.info(
                "FCM skipped: empty token list. title=%s data_keys=%s",
                title,
                sorted(normalized_data.keys()),
            )
            return None

        logger.info(
            "FCM send started. title=%s tokens_total=%s data_keys=%s token_previews=%s",
            title,
            len(tokens),
            sorted(normalized_data.keys()),
            [_mask_token(token) for token in tokens],
        )

        badges = _badges_for(tokens, badge_for)
        try:
            if badges is None:
                message = messaging.MulticastMessage(
                    notification=messaging.Notification(
                        title=title,
                        body=body,
                    ),
                    data=normalized_data,
                    android=_android_config(android_channel_id),
                    apns=_apns_config(title, body),
                    tokens=tokens,
                )
                response = messaging.send_each_for_multicast(message, app=app)
            else:
                messages = [
                    messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                        ),
                        data=normalized_data,
                        android=_android_config(
                            android_channel_id, badge=badges.get(token)
                        ),
                        apns=_apns_config(title, body, badge=badges.get(token)),
                        token=token,
                    )
                    for token in tokens
                ]
                response = messaging.send_each(messages, app=app)
        except Exception:
            logger.exception(
                "FCM send failed before per-token response. title=%s tokens_total=%s data_keys=%s",
                title,
                len(tokens),
                sorted(normalized_data.keys()),
            )
            return None

        invalid_tokens: list[str] = []
        for idx, send_response in enumerate(response.responses):
            if send_response.success:
                logger.info(
                    "FCM token delivery succeeded. token=%s",
                    _mask_token(tokens[idx] if idx < len(tokens) else "unknown"),
                )
                continue

            token = tokens[idx] if idx < len(tokens) else "unknown"
            error_code = getattr(getattr(send_response, "exception", None), "code", None)
            error_message = str(getattr(send_response, "exception", "Unknown error"))

            logger.warning(
                "FCM token delivery failed. token=%s code=%s error=%s",
                _mask_token(token),
                error_code,
                error_message,
            )

            if error_code in {
                "registration-token-not-registered",
                "invalid-registration-token",
                "invalid-argument",
                "unregistered",
            }:
                invalid_tokens.append(token)

        if invalid_tokens:
            deactivate = deactivate_invalid or FCMService._deactivate_invalid_tokens
            try:
                deactivate(invalid_tokens)
            except Exception:
                # The message went out; failing to tidy up after it must not
                # turn a partly-successful send into a raised exception.
                logger.exception(
                    "FCM invalid tokens could not be deactivated: count=%s",
                    len(invalid_tokens),
                )
            else:
                logger.info("FCM invalid tokens deactivated: count=%s", len(invalid_tokens))

        logger.info(
            "FCM send finished. title=%s success=%s failure=%s tokens_total=%s invalidated=%s",
            title,
            response.success_count,
            response.failure_count,
            len(tokens),
            len(invalid_tokens),
        )
        return response


class NotificationService:
    @staticmethod
    def _normalize_data(data: dict | None) -> dict:
        if not data:
            return {}
        normalized: dict[str, str] = {}
        for key, value in data.items():
            if value is None:
                continue
            normalized[str(key)] = str(value)
        return normalized

    @staticmethod
    def send_to_client(
        client,
        title: str,
        message: str,
        notification_type: str,
        data: dict | None = None,
    ):
        normalized_data = NotificationService._normalize_data(data)

        logger.info(
            "Client notification requested. client_id=%s notification_type=%s title=%s data_keys=%s",
            getattr(client, "id", None),
            notification_type,
            title,
            sorted(normalized_data.keys()),
        )

        notification = create_notification(
            recipient_user_id=getattr(client, "id", None),
            recipient_role="client",
            title=title,
            push_message=message,
            notification_type=notification_type,
            status="pending",
            is_for_every_one=False,
            payload=dict(data) if data else None,
        )

        tokens: list[str] = []
        token_rows = fetch_all(
            """
            SELECT fcm_token
            FROM public.users
            WHERE id = %s
              AND role = 'client'
              AND fcm_token IS NOT NULL
            """,
            [getattr(client, "id", None)],
        )
        tokens = [row["fcm_token"] for row in token_rows]

        logger.info(
            "Client notification tokens loaded. client_id=%s tokens_total=%s",
            getattr(client, "id", None),
            len(tokens),
        )

        response = FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=message,
            data=normalized_data,
        )

        if notification and response and response.success_count > 0:
            execute(
                """
                UPDATE public.notification
                SET status = 'sent'
                WHERE id = %s
                """,
                [notification["id"]],
            )
            logger.info(
                "Client notification marked sent. client_id=%s notification_id=%s success=%s failure=%s",
                getattr(client, "id", None),
                notification.get("id"),
                response.success_count,
                response.failure_count,
            )
        else:
            logger.warning(
                "Notification remains pending: no successful FCM delivery. client_id=%s notification_id=%s",
                getattr(client, "id", None),
                notification.get("id") if notification else None,
            )

        return notification

    @staticmethod
    def send_to_partner(
        partner,
        title: str,
        message: str,
        notification_type: str = "system",
        data: dict | None = None,
    ):
        """Send notification to partner and save to history"""
        normalized_data = NotificationService._normalize_data(data)
        logger.info(
            "Partner notification requested. partner_id=%s notification_type=%s title=%s data_keys=%s",
            getattr(partner, "id", None),
            notification_type,
            title,
            sorted(normalized_data.keys()),
        )

        notification = create_notification(
            recipient_user_id=getattr(partner, "id", None),
            recipient_role="partner",
            title=title,
            push_message=message,
            notification_type=notification_type,
            status="pending",
            is_for_every_one=False,
            payload=dict(data) if data else None,
        )
        logger.info(
            "Partner notification saved to normalized table. partner=%s title=%s",
            getattr(partner, "id", None),
            title,
        )

        # Send push notification
        tokens: list[str] = []
        token_rows = fetch_all(
            """
            SELECT fcm_token
            FROM public.users
            WHERE id = %s
              AND role = 'partner'
              AND fcm_token IS NOT NULL
            """,
            [getattr(partner, "id", None)],
        )
        tokens = [row["fcm_token"] for row in token_rows]

        logger.info(
            "Partner notification tokens loaded. partner_id=%s tokens_total=%s",
            getattr(partner, "id", None),
            len(tokens),
        )

        response = FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=message,
            data=normalized_data,
        )
        if response:
            logger.info(
                "Partner notification send result. partner_id=%s success=%s failure=%s",
                getattr(partner, "id", None),
                response.success_count,
                response.failure_count,
            )
        else:
            logger.warning(
                "Partner notification send skipped or produced no response. partner_id=%s",
                getattr(partner, "id", None),
            )

        if notification and response and response.success_count > 0:
            execute(
                """
                UPDATE public.notification
                SET status = 'sent'
                WHERE id = %s
                """,
                [notification["id"]],
            )
            logger.info(
                "Partner notification marked sent. partner_id=%s notification_id=%s success=%s failure=%s",
                getattr(partner, "id", None),
                notification.get("id"),
                response.success_count,
                response.failure_count,
            )

        return notification

    @staticmethod
    def send_broadcast(notification):
        message = messaging.send(
            messaging.Message(
                topic="all_clients",
                notification=messaging.Notification(
                    title=getattr(notification, "title", None),
                    body=getattr(notification, "push_message", None),
                ),
                data={
                    "type": "system",
                },
            )
        )
        logger.info("Response: %s", message)
