from django.urls import path
from .views import AdminLoginView, AdminMeView, AdminRefreshTokenView, AdminRegisterView
from .users_views import AdminClientsListView, AdminPartnersListView
from .b2b_admin_views import (
    AdminB2BCompaniesView,
    AdminB2BCompanyDetailView,
    AdminB2BOwnershipRequestsView,
    AdminB2BOwnershipRequestView,
    AdminB2BUsersView,
    AdminB2BSupportThreadsView,
    AdminB2BSupportThreadView,
)
from .ccd_views import (
    CcdApprovalsView,
    CcdAuditView,
    CcdCallsView,
    CcdCompaniesView,
    CcdCompanyDetailView,
    CcdEmployeeActiveView,
    CcdEmployeesView,
    CcdTicketMessagesView,
    CcdTicketsView,
    CcdWorkspaceActiveView,
    CcdWorkspacesView,
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
    # The other end of the mobile app's ownership-transfer / close-company
    # requests — see `WorkspaceOwnershipRequestView`.
    path('b2b/ownership-requests/', AdminB2BOwnershipRequestsView.as_view(),
         name='admin-b2b-ownership-requests'),
    path('b2b/ownership-requests/<int:request_id>/decide/',
         AdminB2BOwnershipRequestView.as_view(),
         name='admin-b2b-ownership-request-decide'),
    # ─── Call Center Desk (weelccd) ──────────────────────────────────────────
    # The desk is a separate service with its own UI and operator records; it keeps no
    # copy of the B2B data and reads all of it from here. See apps/admin_auth/ccd_views.py.
    path('ccd/companies/', CcdCompaniesView.as_view(), name='ccd-companies'),
    path('ccd/companies/<int:company_id>/', CcdCompanyDetailView.as_view(), name='ccd-company-detail'),
    path('ccd/workspaces/', CcdWorkspacesView.as_view(), name='ccd-workspaces'),
    path('ccd/workspaces/<int:workspace_id>/active/', CcdWorkspaceActiveView.as_view(), name='ccd-workspace-active'),
    path('ccd/employees/', CcdEmployeesView.as_view(), name='ccd-employees'),
    path('ccd/employees/<int:employee_id>/active/', CcdEmployeeActiveView.as_view(), name='ccd-employee-active'),
    path('ccd/calls/', CcdCallsView.as_view(), name='ccd-calls'),
    path('ccd/audit/', CcdAuditView.as_view(), name='ccd-audit'),
    path('ccd/approvals/', CcdApprovalsView.as_view(), name='ccd-approvals'),
    path('ccd/tickets/', CcdTicketsView.as_view(), name='ccd-tickets'),
    path('ccd/tickets/<int:employee_id>/', CcdTicketMessagesView.as_view(), name='ccd-ticket-messages'),
    # Adventure Activities
    path('activities/', AdminActivityListView.as_view(), name='admin-activities-list'),
    path('activities/<uuid:guid>/', AdminActivityDetailView.as_view(), name='admin-activity-detail'),
    path('activities/<uuid:guid>/calendar/', AdminActivityCalendarView.as_view(), name='admin-activity-calendar'),
]
