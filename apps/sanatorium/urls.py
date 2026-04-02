from django.urls import path

from .views import (
    # Lookups
    MedicalSpecializationListView,
    TreatmentListView,
    RoomTypeListView,
    PackageTypeListView,
    RoomAmenityListView,
    # Public
    SanatoriumListView,
    SanatoriumDetailView,
    SanatoriumRoomListView,
    SanatoriumRoomDetailView,
    RoomCalendarDateListView,
    RoomCalendarDateBlockView,
    RoomCalendarDateUnblockView,
    SanatoriumReviewListCreateView,
    SanatoriumFavoriteToggleView,
    # Partner
    PartnerSanatoriumListCreateView,
    PartnerSanatoriumImageUploadView,
    # Client booking
    ClientSanatoriumBookingListCreateView,
    ClientSanatoriumBookingDetailView,
    ClientSanatoriumBookingCancelView,
    ClientSanatoriumBookingHistoryView,
    # Partner booking
    PartnerSanatoriumBookingListView,
    PartnerSanatoriumBookingAcceptView,
    PartnerSanatoriumBookingCancelView,
    PartnerSanatoriumBookingCompleteView,
    PartnerSanatoriumBookingNoShowView,
)


urlpatterns = [
    # ── Lookup / reference data ──
    path("specializations/", MedicalSpecializationListView.as_view(), name="specialization-list"),
    path("treatments/", TreatmentListView.as_view(), name="treatment-list"),
    path("room-types/", RoomTypeListView.as_view(), name="room-type-list"),
    path("package-types/", PackageTypeListView.as_view(), name="package-type-list"),
    path("amenities/", RoomAmenityListView.as_view(), name="amenity-list"),

    # ── Sanatorium list / detail ──
    path("", SanatoriumListView.as_view(), name=".sanatorium-list"),
    path("<uuid:sanatorium_id>/", SanatoriumDetailView.as_view(), name=".sanatorium-detail"),

    # ── Rooms ──
    path("<uuid:sanatorium_id>/rooms/", SanatoriumRoomListView.as_view(), name=".sanatorium-room-list"),
    path("<uuid:sanatorium_id>/rooms/<uuid:room_id>/", SanatoriumRoomDetailView.as_view(), name=".sanatorium-room-detail"),

    # ── Room calendar ──
    path("<uuid:sanatorium_id>/rooms/<uuid:room_id>/calendar/", RoomCalendarDateListView.as_view(), name="room-calendar-list"),
    path("<uuid:sanatorium_id>/rooms/<uuid:room_id>/calendar/block/", RoomCalendarDateBlockView.as_view(), name="room-calendar-block"),
    path("<uuid:sanatorium_id>/rooms/<uuid:room_id>/calendar/unblock/", RoomCalendarDateUnblockView.as_view(), name="room-calendar-unblock"),

    # ── Reviews ──
    path("<uuid:sanatorium_id>/reviews/", SanatoriumReviewListCreateView.as_view(), name=".sanatorium-review-list-create"),

    # ── Favorites ──
    path("<uuid:sanatorium_id>/favorite/", SanatoriumFavoriteToggleView.as_view(), name=".sanatorium-favorite-toggle"),

    # ── Partner CRUD ──
    path("partner/", PartnerSanatoriumListCreateView.as_view(), name="partner-.sanatorium-list-create"),
    path("partner/<uuid:sanatorium_id>/images/", PartnerSanatoriumImageUploadView.as_view(), name="partner-.sanatorium-image-upload"),

    # ── Client booking ──
    path("booking/client/", ClientSanatoriumBookingListCreateView.as_view(), name="client-.sanatorium-booking-list-create"),
    path("booking/client/history/", ClientSanatoriumBookingHistoryView.as_view(), name="client-.sanatorium-booking-history"),
    path("booking/client/<uuid:booking_id>/", ClientSanatoriumBookingDetailView.as_view(), name="client-.sanatorium-booking-detail"),
    path("booking/client/<uuid:booking_id>/cancel/", ClientSanatoriumBookingCancelView.as_view(), name="client-.sanatorium-booking-cancel"),

    # ── Partner booking management ──
    path("booking/partner/", PartnerSanatoriumBookingListView.as_view(), name="partner-.sanatorium-booking-list"),
    path("booking/partner/<uuid:booking_id>/accept/", PartnerSanatoriumBookingAcceptView.as_view(), name="partner-.sanatorium-booking-accept"),
    path("booking/partner/<uuid:booking_id>/cancel/", PartnerSanatoriumBookingCancelView.as_view(), name="partner-.sanatorium-booking-cancel"),
    path("booking/partner/<uuid:booking_id>/complete/", PartnerSanatoriumBookingCompleteView.as_view(), name="partner-.sanatorium-booking-complete"),
    path("booking/partner/<uuid:booking_id>/no_show/", PartnerSanatoriumBookingNoShowView.as_view(), name="partner-.sanatorium-booking-no-show"),
]
