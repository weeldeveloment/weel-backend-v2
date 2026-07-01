from django.urls import path
from .views import AdminLoginView, AdminMeView, AdminRefreshTokenView, AdminRegisterView
from .users_views import AdminClientsListView, AdminPartnersListView
from .hotel_views import (
    AdminHotelListView,
    AdminHotelDetailView,
    AdminHotelClassifyView,
    AdminHotelRoomInventoryView,
    AdminHotelRoomTypesView,
    AdminHotelCalendarView,
    AdminHotelBookingsView,
    AdminHotelBookingDetailView,
    AdminHotelBookingCreateView,
    AdminHotelBookingMoveView,
    AdminHotelBookingAcceptView,
    AdminHotelBookingCancelView,
    AdminHotelBookingCheckInView,
    AdminHotelBookingCheckOutView,
    AdminHotelRatesView,
    AdminHotelReviewsView,
    AdminReviewRespondView,
    AdminReviewHideView,
    AdminB2BCompaniesView,
    AdminB2BCompanyDetailView,
    AdminB2BUsersView,
)

urlpatterns = [
    path('login/', AdminLoginView.as_view(), name='admin-login'),
    path('me/', AdminMeView.as_view(), name='admin-me'),
    path('token/refresh/', AdminRefreshTokenView.as_view(), name='admin-token-refresh'),
    path('register/', AdminRegisterView.as_view(), name='admin-register'),
    # Users management
    path('users/clients/', AdminClientsListView.as_view(), name='admin-clients-list'),
    path('users/partners/', AdminPartnersListView.as_view(), name='admin-partners-list'),
    # Hotel Management
    path('hotels/', AdminHotelListView.as_view(), name='admin-hotels-list'),
    path('hotels/<path:property_id>/', AdminHotelDetailView.as_view(), name='admin-hotel-detail'),
    path('hotels/<path:property_id>/classify/', AdminHotelClassifyView.as_view(), name='admin-hotel-classify'),
    path('hotels/<path:property_id>/rooms/', AdminHotelRoomInventoryView.as_view(), name='admin-hotel-rooms'),
    path('hotels/<path:property_id>/room-types/', AdminHotelRoomTypesView.as_view(), name='admin-hotel-room-types'),
    path('hotels/<path:property_id>/calendar/', AdminHotelCalendarView.as_view(), name='admin-hotel-calendar'),
    path('hotels/<path:property_id>/bookings/', AdminHotelBookingsView.as_view(), name='admin-hotel-bookings'),
    path('hotels/<path:property_id>/bookings/create/', AdminHotelBookingCreateView.as_view(), name='admin-hotel-booking-create'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/', AdminHotelBookingDetailView.as_view(), name='admin-hotel-booking-detail'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/accept/', AdminHotelBookingAcceptView.as_view(), name='admin-hotel-booking-accept'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/cancel/', AdminHotelBookingCancelView.as_view(), name='admin-hotel-booking-cancel'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/check-in/', AdminHotelBookingCheckInView.as_view(), name='admin-hotel-booking-check-in'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/check-out/', AdminHotelBookingCheckOutView.as_view(), name='admin-hotel-booking-check-out'),
    path('hotels/<path:property_id>/bookings/<int:booking_id>/move/', AdminHotelBookingMoveView.as_view(), name='admin-hotel-booking-move'),
    path('hotels/<path:property_id>/rates/', AdminHotelRatesView.as_view(), name='admin-hotel-rates'),
    path('hotels/<path:property_id>/reviews/', AdminHotelReviewsView.as_view(), name='admin-hotel-reviews'),
    path('hotels/<path:property_id>/reviews/<int:review_id>/respond/', AdminReviewRespondView.as_view(), name='admin-review-respond'),
    path('hotels/<path:property_id>/reviews/<int:review_id>/hide/', AdminReviewHideView.as_view(), name='admin-review-hide'),
    # B2B Companies
    path('b2b/companies/', AdminB2BCompaniesView.as_view(), name='admin-b2b-companies'),
    path('b2b/companies/<int:company_id>/', AdminB2BCompanyDetailView.as_view(), name='admin-b2b-company-detail'),
    path('b2b/companies/<int:company_id>/users/', AdminB2BUsersView.as_view(), name='admin-b2b-users'),
]
