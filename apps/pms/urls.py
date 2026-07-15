from django.urls import path

from .views import (
    AnalyticsExportView,
    AnalyticsView,
    BookingAcceptView,
    BookingCancelView,
    BookingCheckInView,
    BookingCheckOutView,
    BookingHistoryView,
    BookingListCreateView,
    BookingMealPlanView,
    BookingMoveView,
    BookingRetrieveView,
    CalendarBlockView,
    CalendarHoldView,
    CalendarUnblockView,
    CalendarUnholdView,
    CalendarView,
    GuestListCreateView,
    GuestRetrieveUpdateView,
    PropertyImageCreateView,
    PropertyImageDeleteView,
    PropertyListCreateView,
    PropertyRetrieveUpdateDestroyView,
    ReviewComplainView,
    ReviewListCreateView,
    ReviewRespondView,
    RoomListCreateView,
    RoomMassUpdateView,
    RoomRetrieveUpdateDestroyView,
    RoomTypeListCreateView,
    RoomTypeRetrieveUpdateDestroyView,
)

urlpatterns = [
    # Properties
    path("properties/", PropertyListCreateView.as_view(), name="pms-property-list-create"),
    path("properties/<int:property_id>/", PropertyRetrieveUpdateDestroyView.as_view(), name="pms-property-detail"),
    path("properties/<int:property_id>/images/", PropertyImageCreateView.as_view(), name="pms-property-image-create"),
    path("properties/<int:property_id>/images/<int:image_id>/", PropertyImageDeleteView.as_view(), name="pms-property-image-delete"),

    # Rooms
    path("properties/<int:property_id>/rooms/", RoomListCreateView.as_view(), name="pms-room-list-create"),
    path("properties/<int:property_id>/rooms/mass-update/", RoomMassUpdateView.as_view(), name="pms-room-mass-update"),
    path("properties/<int:property_id>/rooms/<int:room_id>/", RoomRetrieveUpdateDestroyView.as_view(), name="pms-room-detail"),

    # Room Types
    path("properties/<int:property_id>/room-types/", RoomTypeListCreateView.as_view(), name="pms-room-type-list-create"),
    path("properties/<int:property_id>/room-types/<int:room_type_id>/", RoomTypeRetrieveUpdateDestroyView.as_view(), name="pms-room-type-detail"),

    # Calendar
    path("properties/<int:property_id>/calendar/", CalendarView.as_view(), name="pms-calendar"),
    path("properties/<int:property_id>/calendar/block/", CalendarBlockView.as_view(), name="pms-calendar-block"),
    path("properties/<int:property_id>/calendar/unblock/", CalendarUnblockView.as_view(), name="pms-calendar-unblock"),
    path("properties/<int:property_id>/calendar/hold/", CalendarHoldView.as_view(), name="pms-calendar-hold"),
    path("properties/<int:property_id>/calendar/unhold/", CalendarUnholdView.as_view(), name="pms-calendar-unhold"),

    # Guests
    path("guests/", GuestListCreateView.as_view(), name="pms-guest-list-create"),
    path("guests/<int:guest_id>/", GuestRetrieveUpdateView.as_view(), name="pms-guest-detail"),

    # Bookings
    path("properties/<int:property_id>/bookings/", BookingListCreateView.as_view(), name="pms-booking-list-create"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/", BookingRetrieveView.as_view(), name="pms-booking-detail"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/accept/", BookingAcceptView.as_view(), name="pms-booking-accept"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/cancel/", BookingCancelView.as_view(), name="pms-booking-cancel"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/check-in/", BookingCheckInView.as_view(), name="pms-booking-check-in"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/check-out/", BookingCheckOutView.as_view(), name="pms-booking-check-out"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/move/", BookingMoveView.as_view(), name="pms-booking-move"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/meal-plan/", BookingMealPlanView.as_view(), name="pms-booking-meal-plan"),
    path("properties/<int:property_id>/bookings/<int:booking_id>/history/", BookingHistoryView.as_view(), name="pms-booking-history"),

    # Reviews
    path("properties/<int:property_id>/reviews/", ReviewListCreateView.as_view(), name="pms-review-list-create"),
    path("properties/<int:property_id>/reviews/<int:review_id>/respond/", ReviewRespondView.as_view(), name="pms-review-respond"),
    path("properties/<int:property_id>/reviews/<int:review_id>/complain/", ReviewComplainView.as_view(), name="pms-review-complain"),

    # Analytics
    path("properties/<int:property_id>/analytics/", AnalyticsView.as_view(), name="pms-analytics"),
    path("properties/<int:property_id>/analytics/export/", AnalyticsExportView.as_view(), name="pms-analytics-export"),
]
