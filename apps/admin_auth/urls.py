from django.urls import path
from .views import AdminLoginView, AdminMeView, AdminRefreshTokenView, AdminRegisterView
from .users_views import AdminClientsListView, AdminPartnersListView
from .b2b_admin_views import (
    AdminB2BCompaniesView,
    AdminB2BCompanyDetailView,
    AdminB2BUsersView,
    AdminB2BSupportThreadsView,
    AdminB2BSupportThreadView,
)
from apps.activities.views import (
    AdminActivityCalendarView,
    AdminActivityDetailView,
    AdminActivityListView,
)

urlpatterns = [
    path('login/', AdminLoginView.as_view(), name='admin-login'),
    path('me/', AdminMeView.as_view(), name='admin-me'),
    path('token/refresh/', AdminRefreshTokenView.as_view(), name='admin-token-refresh'),
    path('register/', AdminRegisterView.as_view(), name='admin-register'),
    # Users management
    path('users/clients/', AdminClientsListView.as_view(), name='admin-clients-list'),
    path('users/partners/', AdminPartnersListView.as_view(), name='admin-partners-list'),
    # B2B Companies
    path('b2b/companies/', AdminB2BCompaniesView.as_view(), name='admin-b2b-companies'),
    path('b2b/companies/<int:company_id>/', AdminB2BCompanyDetailView.as_view(), name='admin-b2b-company-detail'),
    path('b2b/companies/<int:company_id>/users/', AdminB2BUsersView.as_view(), name='admin-b2b-users'),

    # The other end of the mobile app's "Yordam markazi".
    path('b2b/support/', AdminB2BSupportThreadsView.as_view(), name='admin-b2b-support'),
    path('b2b/support/<int:employee_id>/', AdminB2BSupportThreadView.as_view(),
         name='admin-b2b-support-thread'),
    # Adventure Activities
    path('activities/', AdminActivityListView.as_view(), name='admin-activities-list'),
    path('activities/<uuid:guid>/', AdminActivityDetailView.as_view(), name='admin-activity-detail'),
    path('activities/<uuid:guid>/calendar/', AdminActivityCalendarView.as_view(), name='admin-activity-calendar'),
]
