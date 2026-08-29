"""Send one push to one token and print exactly what Firebase said about it.

The point of this command is the error, not the notification. A push that
"just does not arrive" has a handful of causes that all look identical from a
phone, and each of them is a distinct, named FCM error that the normal send
path swallows into a log line nobody is watching:

  third-party-auth-error   No APNs key (or the wrong one) on the Firebase
                           project, or the bundle id of the iOS app registered
                           there does not match the app. This is the usual
                           reason iPhones receive nothing while Android is
                           fine — console configuration, not code.
  senderId-mismatch        The token was issued by a different Firebase
                           project than the one this send authenticates as.
  unregistered             The app was uninstalled, or the token rotated and
                           the backend still holds the old one.

Usage, on the server, inside the container:

    python manage.py send_test_push --token <FCM token> --b2b

`--b2b` sends from the workspace Firebase project, which is the only project
that can address a token the workspace app produced. Without it the send goes
from the default consumer project — useful only for the consumer apps.

The token itself is easiest to read straight out of the database:

    SELECT id, fcm_token FROM b2b_employee WHERE fcm_token IS NOT NULL;
"""

from django.core.management.base import BaseCommand, CommandError
from firebase_admin import messaging

from apps.notification.service import (
    B2B_ANDROID_CHANNEL,
    _android_config,
    _apns_config,
    b2b_firebase_app,
)


class Command(BaseCommand):
    help = "Send one test push to one FCM token and report Firebase's answer."

    def add_arguments(self, parser):
        parser.add_argument("--token", required=True, help="The device's FCM token.")
        parser.add_argument(
            "--b2b",
            action="store_true",
            help="Send from the B2B Firebase project rather than the default one.",
        )
        parser.add_argument("--title", default="Weel test")
        parser.add_argument("--body", default="Push tekshiruvi")

    def handle(self, *args, **options):
        token = options["token"].strip()
        title = options["title"]
        body = options["body"]

        app = None
        channel = None
        if options["b2b"]:
            try:
                app = b2b_firebase_app()
            except Exception as error:
                raise CommandError(str(error)) from error
            channel = B2B_ANDROID_CHANNEL

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={"type": "other"},
            android=_android_config(channel),
            apns=_apns_config(title, body),
            token=token,
        )

        self.stdout.write(
            f"Sending from {'the B2B project' if app else 'the default project'} "
            f"to {token[:8]}...{token[-4:]}"
        )
        try:
            message_id = messaging.send(message, app=app)
        except Exception as error:
            code = getattr(error, "code", None)
            self.stdout.write(self.style.ERROR(f"Rejected: code={code} {error}"))
            raise CommandError("The send failed — the code above is the cause.") from error

        self.stdout.write(self.style.SUCCESS(f"Accepted by FCM: {message_id}"))
        self.stdout.write(
            "Accepted means FCM took it, not that the phone drew it. If nothing "
            "appears on an iPhone after this, the push reached APNs and was "
            "dropped there: check that notifications are on for the app in "
            "Settings and that the phone is not in a Focus mode."
        )
