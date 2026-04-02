from django.urls import path

from .views import (
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
