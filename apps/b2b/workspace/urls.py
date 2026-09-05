from django.urls import include, path

from apps.b2b.mail.urls import notification_urlpatterns
from apps.b2b.workspace.access_views import (
    WorkspaceAccessCatalogueView,
    WorkspaceArchiveView,
    WorkspaceAuditView,
    WorkspaceDeleteRequestDecideView,
    WorkspaceDeleteRequestView,
    WorkspaceEmployeeAccessView,
    WorkspaceEmployeeRemoveView,
    WorkspaceOwnershipRequestView,
    WorkspaceRoleDetailView,
    WorkspacePurgeView,
    WorkspaceRestoreView,
    WorkspaceRoleListView,
    WorkspaceTrashView,
)
from apps.b2b.workspace.analyst_views import (
    AnalystDiscussView,
    AnalystReportListView,
    AnalystReportView,
    AnalystSeenView,
    AnalystView,
    WeelAiChatView,
)
from apps.b2b.workspace.assistant import (
    AssistantConnectionView,
    AssistantMessagesView,
    AssistantView,
)
from apps.b2b.workspace.inventory_views import (
    WorkspaceCategoryDetailView,
    WorkspaceCategoryListCreateView,
    WorkspaceDocumentCancelView,
    WorkspaceDocumentConfirmView,
    WorkspaceDocumentDetailView,
    WorkspaceDocumentListCreateView,
    WorkspaceDocumentPreviewView,
    WorkspaceDocumentReceiveView,
    WorkspaceDocumentSendView,
    WorkspaceGenerateCodeView,
    WorkspaceInventoryExportView,
    WorkspaceInventoryImportCommitView,
    WorkspaceInventoryImportPreviewView,
    WorkspaceInventorySettingsView,
    WorkspaceInventorySummaryView,
    WorkspaceMovementListCreateView,
    WorkspacePendingSalesView,
    WorkspaceProductDetailView,
    WorkspaceProductListCreateView,
    WorkspaceProductMovementsView,
    WorkspaceProductPhotoView,
    WorkspaceProductPriceHistoryView,
    WorkspaceSupplierDetailView,
    WorkspaceSupplierListCreateView,
    WorkspaceWarehouseDetailView,
    WorkspaceWarehouseListCreateView,
)
from apps.b2b.workspace.calls_views import (
    WorkspaceCallAcceptView,
    WorkspaceCallDeclineView,
    WorkspaceCallDetailView,
    WorkspaceCallEndView,
    WorkspaceCallHistoryView,
    WorkspaceCallIncomingView,
    WorkspaceCallListCreateView,
    WorkspaceCallTokenView,
)
from apps.b2b.workspace.conferences_views import (
    WorkspaceConferenceDetailView,
    WorkspaceConferenceEndView,
    WorkspaceConferenceJoinView,
    WorkspaceConferenceListCreateView,
)
from apps.b2b.workspace.joining_views import (
    AccountDeletionPreviewView,
    AccountDeviceTokenView,
    AccountJoinRequestView,
    AccountMeView,
    AccountOpenWorkspaceView,
    AccountOrgWorkspacesView,
    AccountUsernameCheckView,
    AccountUsernameSuggestionView,
    AccountWorkspacesView,
    InvitePreviewView,
    JoinCodeView,
    WorkspaceInviteListCreateView,
    WorkspaceInviteRevokeView,
    WorkspaceJoinRequestDecideView,
    WorkspaceJoinRequestListView,
    WorkspaceSearchView,
)
from apps.b2b.workspace.secondment_views import (
    WorkspaceOrgPeopleView,
    WorkspaceRequestListCreateView,
    WorkspaceRequestRespondView,
    WorkspaceSwitchView,
)
from apps.b2b.workspace.views import (
    AccountTokenRefreshView,
    WorkspaceAppVersionView,
    WorkspaceAttendanceAbsenceView,
    WorkspaceAttendanceCheckInView,
    WorkspaceAttendanceCheckOutView,
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
    WorkspaceLeadAssignView,
    WorkspaceLeadClaimView,
    WorkspaceLeadCommentView,
    WorkspaceLeadCompleteView,
    WorkspaceLeadDetailView,
    WorkspaceLeadDueDateView,
    WorkspaceLeadQualityView,
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
    WorkspaceNoteDetailView,
    WorkspaceNoteListCreateView,
    WorkspaceNoteVoiceView,
    WorkspaceStorageView,
    WorkspaceSupportView,
    WorkspaceSubtaskToggleView,
    WorkspaceTaskActivityFeedView,
    WorkspaceTaskCommentView,
    WorkspaceEmployeeStatsView,
    WorkspaceTaskDetailView,
    WorkspaceTaskFileDetailView,
    WorkspaceTaskFilesView,
    WorkspaceTaskListCreateView,
    WorkspaceTaskStatusView,
    WorkspaceTaskVoiceView,
    WorkspaceMessagePinView,
    WorkspaceMessageReactionView,
    WorkspacePresenceView,
    WorkspaceProfilePhotoView,
    WorkspaceReportView,
    WorkspaceTeamView,
    WorkspaceProfileView,
    WorkspaceUsernameView,
    WorkspaceGroupMemberView,
    WorkspaceGroupMembersView,
    WorkspaceGroupView,
    WorkspaceThreadFlagsView,
    WorkspaceThreadListCreateView,
    WorkspaceThreadReadView,
    WorkspaceTokenRefreshView,
)

urlpatterns = [
    # Asked before the session is: see [WorkspaceAppVersionView].
    path("app-version/", WorkspaceAppVersionView.as_view(), name="ws-app-version"),

    path("auth/login/", WorkspaceLoginView.as_view(), name="ws-login"),
    path("auth/login/verify/", WorkspaceLoginVerifyView.as_view(), name="ws-login-verify"),
    path("auth/token/refresh/", WorkspaceTokenRefreshView.as_view(), name="ws-token-refresh"),
    path("auth/logout/", WorkspaceLogoutView.as_view(), name="ws-logout"),

    path("me/", WorkspaceMeView.as_view(), name="ws-me"),
    path("me/device-token/", WorkspaceDeviceTokenView.as_view(), name="ws-device-token"),
    path("me/profile/", WorkspaceProfileView.as_view(), name="ws-profile"),
    path("me/photo/", WorkspaceProfilePhotoView.as_view(), name="ws-profile-photo"),
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
    path(
        "employees/<int:employee_id>/remove/",
        WorkspaceEmployeeRemoveView.as_view(),
        name="ws-employee-remove",
    ),
    path("audit/", WorkspaceAuditView.as_view(), name="ws-audit"),
    path("archive/", WorkspaceArchiveView.as_view(), name="ws-archive"),
    path(
        "delete-requests/",
        WorkspaceDeleteRequestView.as_view(),
        name="ws-delete-requests",
    ),
    path(
        "delete-requests/<int:request_id>/<str:action>/",
        WorkspaceDeleteRequestDecideView.as_view(),
        name="ws-delete-request-decide",
    ),
    path(
        "company/ownership-requests/",
        WorkspaceOwnershipRequestView.as_view(),
        name="ws-ownership-requests",
    ),
    path("trash/", WorkspaceTrashView.as_view(), name="ws-trash"),
    path(
        "trash/<str:kind>/<int:object_id>/restore/",
        WorkspaceRestoreView.as_view(),
        name="ws-restore",
    ),
    path(
        "trash/<str:kind>/<int:object_id>/",
        WorkspacePurgeView.as_view(),
        name="ws-purge",
    ),

    # Registration and the account session — see `accounts.py`.
    path(
        "account/token/refresh/",
        AccountTokenRefreshView.as_view(),
        name="ws-account-token-refresh",
    ),
    path("account/me/", AccountMeView.as_view(), name="ws-account-me"),
    path(
        "account/username-suggestion/",
        AccountUsernameSuggestionView.as_view(),
        name="ws-account-username-suggestion",
    ),
    path(
        "account/username-check/",
        AccountUsernameCheckView.as_view(),
        name="ws-account-username-check",
    ),
    path(
        "account/workspaces/",
        AccountWorkspacesView.as_view(),
        name="ws-account-workspaces",
    ),
    path(
        "account/me/deletion/",
        AccountDeletionPreviewView.as_view(),
        name="ws-account-deletion-preview",
    ),
    path(
        "account/workspaces/search/",
        WorkspaceSearchView.as_view(),
        name="ws-account-workspace-search",
    ),
    path(
        "account/workspaces/<int:employee_id>/open/",
        AccountOpenWorkspaceView.as_view(),
        name="ws-account-open",
    ),
    path(
        "account/orgs/<int:org_id>/workspaces/",
        AccountOrgWorkspacesView.as_view(),
        name="ws-account-org-workspaces",
    ),
    path(
        "account/join-code/",
        JoinCodeView.as_view(),
        name="ws-account-join-code",
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
    path(
        "account/device-token/",
        AccountDeviceTokenView.as_view(),
        name="ws-account-device-token",
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
    path(
        "employees/<int:employee_id>/stats/",
        WorkspaceEmployeeStatsView.as_view(),
        name="ws-employee-stats",
    ),
    path("presence/", WorkspacePresenceView.as_view(), name="ws-presence"),

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
        "attendance/check-out/",
        WorkspaceAttendanceCheckOutView.as_view(),
        name="ws-attendance-check-out",
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

    # Hisobot va analitika — one screen, one call. See [WorkspaceReportView].
    path("reports/", WorkspaceReportView.as_view(), name="ws-reports"),

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
    path("tasks/<int:task_id>/files/", WorkspaceTaskFilesView.as_view(), name="ws-task-files"),
    path(
        "tasks/<int:task_id>/files/<int:file_id>/",
        WorkspaceTaskFileDetailView.as_view(),
        name="ws-task-file-detail",
    ),
    path(
        "tasks/<int:task_id>/subtasks/<int:subtask_id>/toggle/",
        WorkspaceSubtaskToggleView.as_view(),
        name="ws-subtask-toggle",
    ),

    path("events/", WorkspaceEventListCreateView.as_view(), name="ws-events"),
    path("events/<int:event_id>/", WorkspaceEventDetailView.as_view(), name="ws-event-detail"),
    # Named `ws-note*` so `WorkspaceAPIView.LIVE_SECTIONS` files them under the
    # calendar: the strip sits on that screen, and a note added on one phone
    # should appear on the other without a pull-to-refresh.
    path("notes/", WorkspaceNoteListCreateView.as_view(), name="ws-notes"),
    path("notes/<int:note_id>/", WorkspaceNoteDetailView.as_view(), name="ws-note-detail"),
    path("notes/<int:note_id>/voice/", WorkspaceNoteVoiceView.as_view(), name="ws-note-voice"),

    # Jonli video/audio qo'ng'iroq (Jitsi) — TZ §7. The fixed paths come
    # before `<int:call_id>` so "history" and "incoming" are not read as ids.
    path("calls/", WorkspaceCallListCreateView.as_view(), name="ws-calls"),
    path("calls/history/", WorkspaceCallHistoryView.as_view(), name="ws-calls-history"),
    path("calls/incoming/", WorkspaceCallIncomingView.as_view(), name="ws-calls-incoming"),
    path("calls/<int:call_id>/", WorkspaceCallDetailView.as_view(), name="ws-call"),
    path("calls/<int:call_id>/accept/", WorkspaceCallAcceptView.as_view(), name="ws-call-accept"),
    path("calls/<int:call_id>/decline/", WorkspaceCallDeclineView.as_view(), name="ws-call-decline"),
    path("calls/<int:call_id>/end/", WorkspaceCallEndView.as_view(), name="ws-call-end"),
    path("calls/<int:call_id>/token/", WorkspaceCallTokenView.as_view(), name="ws-call-token"),
    # Conferences — a room many people are invited into at once, off the group
    # thread that carries the invitation card. See `conferences.py`.
    path("conferences/", WorkspaceConferenceListCreateView.as_view(), name="ws-conferences"),
    path("conferences/<int:conference_id>/", WorkspaceConferenceDetailView.as_view(), name="ws-conference"),
    path(
        "conferences/<int:conference_id>/join/",
        WorkspaceConferenceJoinView.as_view(),
        name="ws-conference-join",
    ),
    path(
        "conferences/<int:conference_id>/end/",
        WorkspaceConferenceEndView.as_view(),
        name="ws-conference-end",
    ),

    path("chats/", WorkspaceThreadListCreateView.as_view(), name="ws-chats"),
    # The AI assistant's row under "Saqlangan xabarlar" — every employee's
    # own chat with whichever of Claude / ChatGPT the workspace connected.
    # See `assistant.py`. Not a thread: it lives in the AI tables.
    path("assistant/", AssistantView.as_view(), name="ws-assistant"),
    path("assistant/messages/", AssistantMessagesView.as_view(), name="ws-assistant-messages"),
    # This person's own Claude/ChatGPT key, for every role. The workspace
    # key on the integrations screen stays the fallback.
    path("assistant/connection/", AssistantConnectionView.as_view(), name="ws-assistant-connection"),
    # Weel AI as a chat about the reader's own work — for every role. The
    # reports under `analyst/reports/` stay with whoever runs the company.
    path("analyst/chat/", WeelAiChatView.as_view(), name="ws-analyst-chat"),
    # Weel AI — the built-in analyst at the top right of the chat list. See
    # `analyst.py`. Managers only; `CanReadAnalyst` says why.
    path("analyst/", AnalystView.as_view(), name="ws-analyst"),
    path("analyst/seen/", AnalystSeenView.as_view(), name="ws-analyst-seen"),
    path("analyst/reports/", AnalystReportListView.as_view(), name="ws-analyst-reports"),
    path("analyst/reports/<int:report_id>/", AnalystReportView.as_view(), name="ws-analyst-report"),
    path(
        "analyst/reports/<int:report_id>/discuss/",
        AnalystDiscussView.as_view(),
        name="ws-analyst-report-discuss",
    ),
    path("chats/<int:thread_id>/messages/", WorkspaceMessageView.as_view(), name="ws-chat-messages"),
    path(
        "chats/<int:thread_id>/messages/<int:message_id>/",
        WorkspaceMessageDetailView.as_view(),
        name="ws-chat-message-detail",
    ),
    path(
        "chats/<int:thread_id>/messages/<int:message_id>/pin/",
        WorkspaceMessagePinView.as_view(),
        name="ws-chat-message-pin",
    ),
    path(
        "chats/<int:thread_id>/messages/<int:message_id>/reactions/",
        WorkspaceMessageReactionView.as_view(),
        name="ws-chat-message-reactions",
    ),
    path("chats/<int:thread_id>/read/", WorkspaceThreadReadView.as_view(), name="ws-chat-read"),
    path("chats/<int:thread_id>/flags/", WorkspaceThreadFlagsView.as_view(), name="ws-chat-flags"),
    # The group's own screen. Under "group/" rather than at the thread root so
    # a direct chat's URL space stays empty — there is no such screen for one.
    path("chats/<int:thread_id>/group/", WorkspaceGroupView.as_view(), name="ws-chat-group"),
    path(
        "chats/<int:thread_id>/members/",
        WorkspaceGroupMembersView.as_view(),
        name="ws-chat-members",
    ),
    path(
        "chats/<int:thread_id>/members/<int:employee_id>/",
        WorkspaceGroupMemberView.as_view(),
        name="ws-chat-member-detail",
    ),

    path("support/", WorkspaceSupportView.as_view(), name="ws-support"),

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
    path("leads/<int:lead_id>/due-date/", WorkspaceLeadDueDateView.as_view(), name="ws-lead-due-date"),
    path("leads/<int:lead_id>/quality/", WorkspaceLeadQualityView.as_view(), name="ws-lead-quality"),
    path("leads/<int:lead_id>/assign/", WorkspaceLeadAssignView.as_view(), name="ws-lead-assign"),
    path("leads/<int:lead_id>/comments/", WorkspaceLeadCommentView.as_view(), name="ws-lead-comments"),
    path("leads/<int:lead_id>/items/", WorkspaceLeadItemsView.as_view(), name="ws-lead-items"),
    path(
        "leads/<int:lead_id>/items/<int:item_id>/",
        WorkspaceLeadItemDetailView.as_view(),
        name="ws-lead-item-detail",
    ),
    path("leads/<int:lead_id>/tasks/", WorkspaceLeadTasksView.as_view(), name="ws-lead-tasks"),

    # Stock and catalogue behind the board — see `inventory_views.py`. Every
    # name starts with `ws-inventory` so one LIVE_SECTIONS entry covers them.
    path(
        "inventory/warehouses/",
        WorkspaceWarehouseListCreateView.as_view(),
        name="ws-inventory-warehouses",
    ),
    path(
        "inventory/warehouses/<int:warehouse_id>/",
        WorkspaceWarehouseDetailView.as_view(),
        name="ws-inventory-warehouse-detail",
    ),
    path(
        "inventory/categories/",
        WorkspaceCategoryListCreateView.as_view(),
        name="ws-inventory-categories",
    ),
    path(
        "inventory/categories/<int:category_id>/",
        WorkspaceCategoryDetailView.as_view(),
        name="ws-inventory-category-detail",
    ),
    path(
        "inventory/products/",
        WorkspaceProductListCreateView.as_view(),
        name="ws-inventory-products",
    ),
    path(
        "inventory/products/<int:product_id>/",
        WorkspaceProductDetailView.as_view(),
        name="ws-inventory-product-detail",
    ),
    path(
        "inventory/products/<int:product_id>/movements/",
        WorkspaceProductMovementsView.as_view(),
        name="ws-inventory-product-movements",
    ),
    path(
        "inventory/movements/",
        WorkspaceMovementListCreateView.as_view(),
        name="ws-inventory-movements",
    ),
    path(
        "inventory/summary/",
        WorkspaceInventorySummaryView.as_view(),
        name="ws-inventory-summary",
    ),
    path("inventory/settings/", WorkspaceInventorySettingsView.as_view(), name="ws-inventory-settings"),
    path("inventory/generate/", WorkspaceGenerateCodeView.as_view(), name="ws-inventory-generate"),
    path("inventory/suppliers/", WorkspaceSupplierListCreateView.as_view(), name="ws-inventory-suppliers"),
    path(
        "inventory/suppliers/<int:supplier_id>/",
        WorkspaceSupplierDetailView.as_view(),
        name="ws-inventory-supplier-detail",
    ),
    path(
        "inventory/products/<int:product_id>/photo/",
        WorkspaceProductPhotoView.as_view(),
        name="ws-inventory-product-photo",
    ),
    path(
        "inventory/products/<int:product_id>/prices/",
        WorkspaceProductPriceHistoryView.as_view(),
        name="ws-inventory-product-prices",
    ),
    path("inventory/documents/", WorkspaceDocumentListCreateView.as_view(), name="ws-inventory-documents"),
    path("inventory/documents/pending/", WorkspacePendingSalesView.as_view(), name="ws-inventory-documents-pending"),
    path(
        "inventory/documents/<int:document_id>/",
        WorkspaceDocumentDetailView.as_view(),
        name="ws-inventory-document-detail",
    ),
    path(
        "inventory/documents/<int:document_id>/preview/",
        WorkspaceDocumentPreviewView.as_view(),
        name="ws-inventory-document-preview",
    ),
    path(
        "inventory/documents/<int:document_id>/confirm/",
        WorkspaceDocumentConfirmView.as_view(),
        name="ws-inventory-document-confirm",
    ),
    path(
        "inventory/documents/<int:document_id>/send/",
        WorkspaceDocumentSendView.as_view(),
        name="ws-inventory-document-send",
    ),
    path(
        "inventory/documents/<int:document_id>/receive/",
        WorkspaceDocumentReceiveView.as_view(),
        name="ws-inventory-document-receive",
    ),
    path(
        "inventory/documents/<int:document_id>/cancel/",
        WorkspaceDocumentCancelView.as_view(),
        name="ws-inventory-document-cancel",
    ),
    path("inventory/export/", WorkspaceInventoryExportView.as_view(), name="ws-inventory-export"),
    path(
        "inventory/import/preview/",
        WorkspaceInventoryImportPreviewView.as_view(),
        name="ws-inventory-import-preview",
    ),
    path(
        "inventory/import/commit/",
        WorkspaceInventoryImportCommitView.as_view(),
        name="ws-inventory-import-commit",
    ),

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
    # Outside services plugged into the funnel. Owner/administrator only —
    # the gate is on the views, see `apps/b2b/integrations/permissions.py`.
    path("integrations/", include("apps.b2b.integrations.urls")),
    *notification_urlpatterns,
]
