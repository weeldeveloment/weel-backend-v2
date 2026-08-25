from django.urls import path

from apps.avia.views import (
    AviaBalanceView,
    AviaBookingCancelView,
    AviaBookingDetailView,
    AviaBookingEventsView,
    AviaBookingFiscalizationView,
    AviaBookingListCreateView,
    AviaBookingPaymentPermissionView,
    AviaBookingPaymentView,
    AviaBookingPriceCheckView,
    AviaBookingReceiptView,
    AviaBookingRefreshView,
    AviaBookingRefundAmountView,
    AviaBookingRulesView,
    AviaOfferDetailView,
    AviaOfferFareFamilyView,
    AviaOfferRulesView,
    AviaOfferSearchView,
    AviaScheduleView,
    AviaStatusCallbackView,
)

urlpatterns = [
    # Offers
    path("offers/search/", AviaOfferSearchView.as_view(), name="avia-offer-search"),
    path("offers/<str:offer_id>/", AviaOfferDetailView.as_view(), name="avia-offer-detail"),
    path("offers/<str:offer_id>/fare-family/", AviaOfferFareFamilyView.as_view(), name="avia-offer-fare-family"),
    path("offers/<str:offer_id>/rules/", AviaOfferRulesView.as_view(), name="avia-offer-rules"),

    # Bookings
    path("bookings/", AviaBookingListCreateView.as_view(), name="avia-bookings"),
    path("bookings/<uuid:guid>/", AviaBookingDetailView.as_view(), name="avia-booking-detail"),
    path("bookings/<uuid:guid>/refresh/", AviaBookingRefreshView.as_view(), name="avia-booking-refresh"),
    path("bookings/<uuid:guid>/rules/", AviaBookingRulesView.as_view(), name="avia-booking-rules"),
    path("bookings/<uuid:guid>/check-price/", AviaBookingPriceCheckView.as_view(), name="avia-booking-check-price"),
    path("bookings/<uuid:guid>/payment/", AviaBookingPaymentView.as_view(), name="avia-booking-payment"),
    path(
        "bookings/<uuid:guid>/payment-permission/",
        AviaBookingPaymentPermissionView.as_view(),
        name="avia-booking-payment-permission",
    ),
    path("bookings/<uuid:guid>/fiscalization/", AviaBookingFiscalizationView.as_view(), name="avia-booking-fiscalization"),
    path("bookings/<uuid:guid>/receipt/", AviaBookingReceiptView.as_view(), name="avia-booking-receipt"),
    path("bookings/<uuid:guid>/refund-amount/", AviaBookingRefundAmountView.as_view(), name="avia-booking-refund-amount"),
    path("bookings/<uuid:guid>/cancel/", AviaBookingCancelView.as_view(), name="avia-booking-cancel"),
    path("bookings/<uuid:guid>/events/", AviaBookingEventsView.as_view(), name="avia-booking-events"),

    # Reference and operations
    path("schedule/", AviaScheduleView.as_view(), name="avia-schedule"),
    path("balance/", AviaBalanceView.as_view(), name="avia-balance"),
    path("callback/status/", AviaStatusCallbackView.as_view(), name="avia-status-callback"),
]
