from django.urls import path

from .views import (
    ClientActivityAvailabilityView,
    ClientActivityDetailView,
    ClientActivityListView,
    ClientBookingCancelView,
    ClientBookingListView,
    ClientCreateBookingView,
    PartnerActivityCalendarView,
    PartnerActivityDetailView,
    PartnerActivityImageDetailView,
    PartnerActivityImageListCreateView,
    PartnerActivityListCreateView,
    PartnerBookingCompleteView,
    PartnerResourceDetailView,
    PartnerResourceListCreateView,
    PartnerTariffDetailView,
    PartnerTariffListCreateView,
    PartnerWorkingHoursView,
)

urlpatterns = [
    # Partner
    path("partner/activities/", PartnerActivityListCreateView.as_view(), name="partner-activities"),
    path("partner/activities/<uuid:guid>/", PartnerActivityDetailView.as_view(), name="partner-activity-detail"),
    path("partner/activities/<uuid:guid>/images/", PartnerActivityImageListCreateView.as_view(), name="partner-activity-images"),
    path("partner/activities/<uuid:guid>/images/<int:image_id>/", PartnerActivityImageDetailView.as_view(), name="partner-activity-image-detail"),
    path("partner/activities/<uuid:guid>/tariffs/", PartnerTariffListCreateView.as_view(), name="partner-activity-tariffs"),
    path("partner/tariffs/<uuid:tariff_guid>/", PartnerTariffDetailView.as_view(), name="partner-tariff-detail"),
    path("partner/activities/<uuid:guid>/resources/", PartnerResourceListCreateView.as_view(), name="partner-activity-resources"),
    path("partner/resources/<uuid:resource_guid>/", PartnerResourceDetailView.as_view(), name="partner-resource-detail"),
    path("partner/activities/<uuid:guid>/working-hours/", PartnerWorkingHoursView.as_view(), name="partner-activity-working-hours"),
    path("partner/activities/<uuid:guid>/calendar/", PartnerActivityCalendarView.as_view(), name="partner-activity-calendar"),
    path("partner/bookings/<uuid:booking_guid>/complete/", PartnerBookingCompleteView.as_view(), name="partner-booking-complete"),

    # Client
    path("client/activities/", ClientActivityListView.as_view(), name="client-activities"),
    path("client/activities/<uuid:guid>/", ClientActivityDetailView.as_view(), name="client-activity-detail"),
    path("client/activities/<uuid:guid>/availability/", ClientActivityAvailabilityView.as_view(), name="client-activity-availability"),
    path("client/activities/<uuid:guid>/bookings/", ClientCreateBookingView.as_view(), name="client-activity-create-booking"),
    path("client/bookings/", ClientBookingListView.as_view(), name="client-bookings"),
    path("client/bookings/<uuid:booking_guid>/cancel/", ClientBookingCancelView.as_view(), name="client-booking-cancel"),
]
