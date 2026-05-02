from django.urls import path

from .views import (
    ClientNotificationListView,
    ClientNotificationMarkAllAsReadView,
    ClientNotificationMarkAsReadView,
    FCMTokenUpdateView,
    PartnerFCMTokenUpdateView,
    PartnerNotificationListView,
    PartnerNotificationMarkAsReadView,
    PartnerNotificationMarkAllAsReadView,
)

urlpatterns = [
    path("device/", FCMTokenUpdateView.as_view(), name="update-fcm-token"),
    path(
        "partner/device/",
        PartnerFCMTokenUpdateView.as_view(),
        name="update-partner-fcm-token",
    ),
    path(
        "client/",
        ClientNotificationListView.as_view(),
        name="client-notifications",
    ),
    path(
        "client/read/",
        ClientNotificationMarkAsReadView.as_view(),
        name="mark-client-notifications-read",
    ),
    path(
        "client/read-all/",
        ClientNotificationMarkAllAsReadView.as_view(),
        name="mark-all-client-notifications-read",
    ),
    path(
        "partner/",
        PartnerNotificationListView.as_view(),
        name="partner-notifications",
    ),
    path(
        "partner/read/",
        PartnerNotificationMarkAsReadView.as_view(),
        name="mark-notifications-read",
    ),
    path(
        "partner/read-all/",
        PartnerNotificationMarkAllAsReadView.as_view(),
        name="mark-all-notifications-read",
    ),
]
