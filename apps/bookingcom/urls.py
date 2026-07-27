from django.urls import path

from .views import (
    BookingComConnectionView,
    BookingComManualSyncView,
    BookingComRoomMappingView,
    BookingComStatusView,
)


urlpatterns = [
    path("properties/<int:property_id>/booking-com/connection/", BookingComConnectionView.as_view(), name="bookingcom-connection"),
    path("properties/<int:property_id>/booking-com/mappings/", BookingComRoomMappingView.as_view(), name="bookingcom-room-mappings"),
    path("properties/<int:property_id>/booking-com/sync/", BookingComManualSyncView.as_view(), name="bookingcom-sync"),
    path("properties/<int:property_id>/booking-com/status/", BookingComStatusView.as_view(), name="bookingcom-status"),
]
