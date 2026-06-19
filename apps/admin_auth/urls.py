from django.urls import path
from .views import AdminLoginView, AdminMeView, AdminRefreshTokenView, AdminRegisterView
from .users_views import AdminClientsListView, AdminPartnersListView
from .hotel_views import (
    AdminHotelListView,
    AdminHotelDetailView,
    AdminHotelClassifyView,
    AdminHotelRoomInventoryView,
    AdminHotelCalendarView,
    AdminHotelBookingsView,
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
    path('hotels/<int:property_id>/', AdminHotelDetailView.as_view(), name='admin-hotel-detail'),
    path('hotels/<int:property_id>/classify/', AdminHotelClassifyView.as_view(), name='admin-hotel-classify'),
    path('hotels/<int:property_id>/rooms/', AdminHotelRoomInventoryView.as_view(), name='admin-hotel-rooms'),
    path('hotels/<int:property_id>/calendar/', AdminHotelCalendarView.as_view(), name='admin-hotel-calendar'),
    path('hotels/<int:property_id>/bookings/', AdminHotelBookingsView.as_view(), name='admin-hotel-bookings'),
    path('hotels/<int:property_id>/reviews/', AdminHotelReviewsView.as_view(), name='admin-hotel-reviews'),
    path('hotels/<int:property_id>/reviews/<int:review_id>/respond/', AdminReviewRespondView.as_view(), name='admin-review-respond'),
    path('hotels/<int:property_id>/reviews/<int:review_id>/hide/', AdminReviewHideView.as_view(), name='admin-review-hide'),
    # B2B Companies
    path('b2b/companies/', AdminB2BCompaniesView.as_view(), name='admin-b2b-companies'),
    path('b2b/companies/<int:company_id>/', AdminB2BCompanyDetailView.as_view(), name='admin-b2b-company-detail'),
    path('b2b/companies/<int:company_id>/users/', AdminB2BUsersView.as_view(), name='admin-b2b-users'),
]
