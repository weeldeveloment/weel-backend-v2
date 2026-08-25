from django.urls import path

from apps.hotels.views import (
    HotelBalanceView,
    HotelBookingCancelView,
    HotelBookingConfirmView,
    HotelBookingDetailView,
    HotelBookingEventsView,
    HotelBookingListCreateView,
    HotelBookingRefreshView,
    HotelBookingRoomsView,
    HotelCityListView,
    HotelDetailView,
    HotelListView,
    HotelQuoteView,
    HotelReferenceView,
    HotelSearchView,
    HotelSyncStatusView,
)

urlpatterns = [
    # Catalogue (synced)
    path("", HotelListView.as_view(), name="hotels-list"),
    path("cities/", HotelCityListView.as_view(), name="hotels-cities"),
    path("reference/<str:name>/", HotelReferenceView.as_view(), name="hotels-reference"),

    # Booking flow (live)
    path("search/", HotelSearchView.as_view(), name="hotels-search"),
    path("quote/", HotelQuoteView.as_view(), name="hotels-quote"),
    path("bookings/", HotelBookingListCreateView.as_view(), name="hotels-bookings"),
    path("bookings/<uuid:guid>/", HotelBookingDetailView.as_view(), name="hotels-booking-detail"),
    path("bookings/<uuid:guid>/confirm/", HotelBookingConfirmView.as_view(), name="hotels-booking-confirm"),
    path("bookings/<uuid:guid>/refresh/", HotelBookingRefreshView.as_view(), name="hotels-booking-refresh"),
    path("bookings/<uuid:guid>/cancel/", HotelBookingCancelView.as_view(), name="hotels-booking-cancel"),
    path("bookings/<uuid:guid>/rooms/", HotelBookingRoomsView.as_view(), name="hotels-booking-rooms"),
    path("bookings/<uuid:guid>/events/", HotelBookingEventsView.as_view(), name="hotels-booking-events"),

    # Operations
    path("balance/", HotelBalanceView.as_view(), name="hotels-balance"),
    path("sync-status/", HotelSyncStatusView.as_view(), name="hotels-sync-status"),

    # Last: a bare integer must not shadow any of the literal routes above.
    path("<int:hotel_id>/", HotelDetailView.as_view(), name="hotels-detail"),
]
