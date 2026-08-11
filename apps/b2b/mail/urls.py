from django.urls import path

from apps.b2b.mail.views import (
    B2BNotificationListView,
    B2BNotificationReadView,
    MailAttachmentDownloadView,
    MailAttachmentUploadView,
    MailboxDetailView,
    MailboxListCreateView,
    MailboxResetPasswordView,
    MailDomainDetailView,
    MailDomainListCreateView,
    MailDomainVerifyView,
    MailMeView,
    MailSendView,
    MailSyncNowView,
    MailThreadFlagsView,
    MailThreadListView,
    MailThreadMessagesView,
    MailThreadReadView,
)

urlpatterns = [
    path("me/", MailMeView.as_view(), name="mail-me"),
    path("sync/", MailSyncNowView.as_view(), name="mail-sync"),

    path("threads/", MailThreadListView.as_view(), name="mail-threads"),
    path("threads/<int:thread_id>/messages/", MailThreadMessagesView.as_view(),
         name="mail-thread-messages"),
    path("threads/<int:thread_id>/read/", MailThreadReadView.as_view(), name="mail-thread-read"),
    path("threads/<int:thread_id>/flags/", MailThreadFlagsView.as_view(), name="mail-thread-flags"),

    path("messages/", MailSendView.as_view(), name="mail-send"),

    path("attachments/", MailAttachmentUploadView.as_view(), name="mail-attachment-upload"),
    path("attachments/<int:attachment_id>/", MailAttachmentDownloadView.as_view(),
         name="mail-attachment-download"),

    # Owner-only administration.
    path("domains/", MailDomainListCreateView.as_view(), name="mail-domains"),
    path("domains/<int:domain_id>/", MailDomainDetailView.as_view(), name="mail-domain-detail"),
    path("domains/<int:domain_id>/verify/", MailDomainVerifyView.as_view(),
         name="mail-domain-verify"),

    path("mailboxes/", MailboxListCreateView.as_view(), name="mail-mailboxes"),
    path("mailboxes/<int:mailbox_id>/", MailboxDetailView.as_view(), name="mail-mailbox-detail"),
    path("mailboxes/<int:mailbox_id>/password/", MailboxResetPasswordView.as_view(),
         name="mail-mailbox-password"),
]

# Mounted alongside, not under `mail/`: the feed carries task and chat events
# too, not only mail.
notification_urlpatterns = [
    path("notifications/", B2BNotificationListView.as_view(), name="ws-notifications"),
    path("notifications/read/", B2BNotificationReadView.as_view(), name="ws-notifications-read"),
]
