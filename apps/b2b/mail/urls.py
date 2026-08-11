from django.urls import path

from apps.b2b.mail.views import (
    B2BNotificationListView,
    B2BNotificationReadView,
    MailAccountDetailView,
    MailAccountListCreateView,
    MailAccountReconnectView,
    MailAttachmentDownloadView,
    MailAttachmentUploadView,
    MailGoogleCallbackView,
    MailGoogleStartView,
    MailProviderHintView,
    MailSendView,
    MailSyncNowView,
    MailThreadFlagsView,
    MailThreadListView,
    MailThreadMessagesView,
    MailThreadReadView,
)

urlpatterns = [
    # Connecting an inbox.
    path("accounts/", MailAccountListCreateView.as_view(), name="mail-accounts"),
    path("accounts/<int:account_id>/", MailAccountDetailView.as_view(),
         name="mail-account-detail"),
    path("accounts/<int:account_id>/reconnect/", MailAccountReconnectView.as_view(),
         name="mail-account-reconnect"),
    path("providers/", MailProviderHintView.as_view(), name="mail-providers"),
    path("oauth/google/", MailGoogleStartView.as_view(), name="mail-oauth-google"),
    path("oauth/google/callback/", MailGoogleCallbackView.as_view(),
         name="mail-oauth-google-callback"),

    path("sync/", MailSyncNowView.as_view(), name="mail-sync"),

    path("threads/", MailThreadListView.as_view(), name="mail-threads"),
    path("threads/<int:thread_id>/messages/", MailThreadMessagesView.as_view(),
         name="mail-thread-messages"),
    path("threads/<int:thread_id>/read/", MailThreadReadView.as_view(), name="mail-thread-read"),
    path("threads/<int:thread_id>/flags/", MailThreadFlagsView.as_view(),
         name="mail-thread-flags"),

    path("messages/", MailSendView.as_view(), name="mail-send"),

    path("attachments/", MailAttachmentUploadView.as_view(), name="mail-attachment-upload"),
    path("attachments/<int:attachment_id>/", MailAttachmentDownloadView.as_view(),
         name="mail-attachment-download"),
]

# Mounted alongside, not under `mail/`: the feed carries task and chat events
# too, not only mail.
notification_urlpatterns = [
    path("notifications/", B2BNotificationListView.as_view(), name="ws-notifications"),
    path("notifications/read/", B2BNotificationReadView.as_view(), name="ws-notifications-read"),
]
