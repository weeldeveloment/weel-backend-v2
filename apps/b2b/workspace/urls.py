from django.urls import include, path

from apps.b2b.mail.urls import notification_urlpatterns
from apps.b2b.workspace.access_views import (
    WorkspaceAccessCatalogueView,
    WorkspaceAuditView,
    WorkspaceEmployeeAccessView,
    WorkspaceRoleDetailView,
    WorkspaceRestoreView,
    WorkspaceRoleListView,
    WorkspaceTrashView,
)
from apps.b2b.workspace.joining_views import (
    AccountJoinRequestView,
    AccountMeView,
    AccountOpenWorkspaceView,
    AccountUsernameSuggestionView,
    AccountWorkspacesView,
    InvitePreviewView,
    WorkspaceInviteListCreateView,
    WorkspaceInviteRevokeView,
    WorkspaceJoinRequestDecideView,
    WorkspaceJoinRequestListView,
)
from apps.b2b.workspace.secondment_views import (
    WorkspaceOrgPeopleView,
    WorkspaceRequestListCreateView,
    WorkspaceRequestRespondView,
    WorkspaceSwitchView,
)
from apps.b2b.workspace.views import (
    WorkspaceAttendanceAbsenceView,
    WorkspaceAttendanceCheckInView,
    WorkspaceAttendanceLocationView,
    WorkspaceAttendanceMarkView,
    WorkspaceAttendanceView,
    WorkspaceDeviceTokenView,
    WorkspaceEmployeeMonthlyStatsView,
    WorkspaceEmployeeOfMonthView,
    WorkspaceEventDetailView,
    WorkspaceEventListCreateView,
    WorkspaceFileDetailView,
    WorkspaceFileListCreateView,
    WorkspaceFolderDetailView,
    WorkspaceFolderListCreateView,
    WorkspaceHotelListView,
    WorkspaceLeadAssignView,
    WorkspaceLeadClaimView,
    WorkspaceLeadCommentView,
    WorkspaceLeadCompleteView,
    WorkspaceLeadDetailView,
    WorkspaceLeadItemDetailView,
    WorkspaceLeadItemsView,
    WorkspaceCrmCustomerDetailView,
    WorkspaceCrmCustomerListView,
    WorkspaceCustomerSearchView,
    WorkspaceLeadListCreateView,
    WorkspaceLeadStageView,
    WorkspaceLeadTasksView,
    WorkspaceLoginVerifyView,
    WorkspaceLoginView,
    WorkspaceLogoutView,
    WorkspaceMeView,
    WorkspaceMessageDetailView,
    WorkspaceMessageView,
    WorkspaceStorageView,
    WorkspaceSupportView,
    WorkspaceSubtaskToggleView,
    WorkspaceTaskActivityFeedView,
    WorkspaceTaskCommentView,
    WorkspaceTaskDetailView,
    WorkspaceTaskListCreateView,
    WorkspaceTaskStatusView,
    WorkspaceTaskVoiceView,
    WorkspaceTeamView,
    WorkspaceProfileView,
    WorkspaceUsernameView,
    WorkspaceThreadFlagsView,
    WorkspaceThreadListCreateView,
    WorkspaceThreadReadView,
    WorkspaceTokenRefreshView,
)

urlpatterns = [
    path("auth/login/", WorkspaceLoginView.as_view(), name="ws-login"),
    path("auth/login/verify/", WorkspaceLoginVerifyView.as_view(), name="ws-login-verify"),
    path("auth/token/refresh/", WorkspaceTokenRefreshView.as_view(), name="ws-token-refresh"),
    path("auth/logout/", WorkspaceLogoutView.as_view(), name="ws-logout"),

    path("me/", WorkspaceMeView.as_view(), name="ws-me"),
    path("me/device-token/", WorkspaceDeviceTokenView.as_view(), name="ws-device-token"),
    path("me/profile/", WorkspaceProfileView.as_view(), name="ws-profile"),
    path("me/username/", WorkspaceUsernameView.as_view(), name="ws-username"),

    # Who → where → what. See `access.py`.
    path(
        "access/catalogue/",
        WorkspaceAccessCatalogueView.as_view(),
        name="ws-access-catalogue",
    ),
    path("access/roles/", WorkspaceRoleListView.as_view(), name="ws-access-roles"),
    path(
        "access/roles/<str:code>/",
        WorkspaceRoleDetailView.as_view(),
        name="ws-access-role",
    ),
    path(
        "employees/<int:employee_id>/access/",
        WorkspaceEmployeeAccessView.as_view(),
        name="ws-employee-access",
    ),
    path("audit/", WorkspaceAuditView.as_view(), name="ws-audit"),
    path("trash/", WorkspaceTrashView.as_view(), name="ws-trash"),
    path(
        "trash/<str:kind>/<int:object_id>/restore/",
        WorkspaceRestoreView.as_view(),
        name="ws-restore",
    ),

    # Registration and the account session — see `accounts.py`.
    path("account/me/", AccountMeView.as_view(), name="ws-account-me"),
    path(
        "account/username-suggestion/",
        AccountUsernameSuggestionView.as_view(),
        name="ws-account-username-suggestion",
    ),
    path(
        "account/workspaces/",
        AccountWorkspacesView.as_view(),
        name="ws-account-workspaces",
    ),
    path(
        "account/workspaces/<int:employee_id>/open/",
        AccountOpenWorkspaceView.as_view(),
        name="ws-account-open",
    ),
    path(
        "account/invites/<str:token>/",
        InvitePreviewView.as_view(),
        name="ws-account-invite",
    ),
    path(
        "account/join-requests/",
        AccountJoinRequestView.as_view(),
        name="ws-account-join-request",
    ),

    # The three doors into a workspace, from the workspace's side.
    path("invites/", WorkspaceInviteListCreateView.as_view(), name="ws-invites"),
    path(
        "invites/<int:invite_id>/revoke/",
        WorkspaceInviteRevokeView.as_view(),
        name="ws-invite-revoke",
    ),
    path(
        "join-requests/",
        WorkspaceJoinRequestListView.as_view(),
        name="ws-join-requests",
    ),
    path(
        "join-requests/<int:request_id>/<str:action>/",
        WorkspaceJoinRequestDecideView.as_view(),
        name="ws-join-request-decide",
    ),
    path("team/", WorkspaceTeamView.as_view(), name="ws-team"),

    # Lending somebody to another workspace — see `secondment_views`.
    path("org/people/", WorkspaceOrgPeopleView.as_view(), name="ws-org-people"),
    path("requests/", WorkspaceRequestListCreateView.as_view(), name="ws-requests"),
    path(
        "requests/<int:request_id>/<str:action>/",
        WorkspaceRequestRespondView.as_view(),
        name="ws-request-respond",
    ),
    path("switch/", WorkspaceSwitchView.as_view(), name="ws-switch"),

    path("attendance/", WorkspaceAttendanceView.as_view(), name="ws-attendance"),
    path(
        "attendance/check-in/",
        WorkspaceAttendanceCheckInView.as_view(),
        name="ws-attendance-check-in",
    ),
    path(
        "attendance/absence/",
        WorkspaceAttendanceAbsenceView.as_view(),
        name="ws-attendance-absence",
    ),
    path(
        "attendance/location/",
        WorkspaceAttendanceLocationView.as_view(),
        name="ws-attendance-location",
    ),
    path(
        "attendance/<int:employee_id>/",
        WorkspaceAttendanceMarkView.as_view(),
        name="ws-attendance-mark",
    ),

    path("employee-of-month/", WorkspaceEmployeeOfMonthView.as_view(), name="ws-employee-of-month"),
    path(
        "employee-of-month/stats/",
        WorkspaceEmployeeMonthlyStatsView.as_view(),
        name="ws-employee-of-month-stats",
    ),

    path("tasks/", WorkspaceTaskListCreateView.as_view(), name="ws-tasks"),
    path("tasks/activity/", WorkspaceTaskActivityFeedView.as_view(), name="ws-task-activity-feed"),
    path("tasks/<int:task_id>/", WorkspaceTaskDetailView.as_view(), name="ws-task-detail"),
    path("tasks/<int:task_id>/status/", WorkspaceTaskStatusView.as_view(), name="ws-task-status"),
    path("tasks/<int:task_id>/comments/", WorkspaceTaskCommentView.as_view(), name="ws-task-comments"),
    path("tasks/<int:task_id>/voice/", WorkspaceTaskVoiceView.as_view(), name="ws-task-voice"),
    path(
        "tasks/<int:task_id>/subtasks/<int:subtask_id>/toggle/",
        WorkspaceSubtaskToggleView.as_view(),
        name="ws-subtask-toggle",
    ),

    path("events/", WorkspaceEventListCreateView.as_view(), name="ws-events"),
    path("events/<int:event_id>/", WorkspaceEventDetailView.as_view(), name="ws-event-detail"),

    path("chats/", WorkspaceThreadListCreateView.as_view(), name="ws-chats"),
    path("chats/<int:thread_id>/messages/", WorkspaceMessageView.as_view(), name="ws-chat-messages"),
    path(
        "chats/<int:thread_id>/messages/<int:message_id>/",
        WorkspaceMessageDetailView.as_view(),
        name="ws-chat-message-detail",
    ),
    path("chats/<int:thread_id>/read/", WorkspaceThreadReadView.as_view(), name="ws-chat-read"),
    path("chats/<int:thread_id>/flags/", WorkspaceThreadFlagsView.as_view(), name="ws-chat-flags"),

    path("support/", WorkspaceSupportView.as_view(), name="ws-support"),

    path("hotels/", WorkspaceHotelListView.as_view(), name="ws-hotels"),

    path("customers/", WorkspaceCustomerSearchView.as_view(), name="ws-customers"),
    path("crm/customers/", WorkspaceCrmCustomerListView.as_view(), name="ws-crm-customers"),
    path(
        "crm/customers/<int:customer_id>/",
        WorkspaceCrmCustomerDetailView.as_view(),
        name="ws-crm-customer-detail",
    ),
    path("leads/", WorkspaceLeadListCreateView.as_view(), name="ws-leads"),
    path("leads/<int:lead_id>/", WorkspaceLeadDetailView.as_view(), name="ws-lead-detail"),
    path("leads/<int:lead_id>/claim/", WorkspaceLeadClaimView.as_view(), name="ws-lead-claim"),
    path("leads/<int:lead_id>/complete/", WorkspaceLeadCompleteView.as_view(), name="ws-lead-complete"),
    path("leads/<int:lead_id>/stage/", WorkspaceLeadStageView.as_view(), name="ws-lead-stage"),
    path("leads/<int:lead_id>/assign/", WorkspaceLeadAssignView.as_view(), name="ws-lead-assign"),
    path("leads/<int:lead_id>/comments/", WorkspaceLeadCommentView.as_view(), name="ws-lead-comments"),
    path("leads/<int:lead_id>/items/", WorkspaceLeadItemsView.as_view(), name="ws-lead-items"),
    path(
        "leads/<int:lead_id>/items/<int:item_id>/",
        WorkspaceLeadItemDetailView.as_view(),
        name="ws-lead-item-detail",
    ),
    path("leads/<int:lead_id>/tasks/", WorkspaceLeadTasksView.as_view(), name="ws-lead-tasks"),

    path("folders/", WorkspaceFolderListCreateView.as_view(), name="ws-folders"),
    path(
        "folders/<int:folder_id>/",
        WorkspaceFolderDetailView.as_view(),
        name="ws-folder-detail",
    ),
    path("files/", WorkspaceFileListCreateView.as_view(), name="ws-files"),
    path("storage/", WorkspaceStorageView.as_view(), name="ws-storage"),
    path("files/<int:file_id>/", WorkspaceFileDetailView.as_view(), name="ws-file-detail"),

    path("mail/", include("apps.b2b.mail.urls")),
    *notification_urlpatterns,
]
