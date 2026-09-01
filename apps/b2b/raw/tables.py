B2B_COMPANY_TABLE = "b2b_company"
# The level above a company row. In the product's words a `b2b_company` row is
# a *workspace* and a `b2b_org` row is the *company* that groups them — the
# isolation boundary is `company_id` and always has been, so the new level was
# added above it rather than beside it. See `create_b2b_tables`.
B2B_ORG_TABLE = "b2b_org"
# Somebody lent to another workspace: the ask, and the standing it creates.
B2B_WORKSPACE_REQUEST_TABLE = "b2b_workspace_request"
B2B_WORKSPACE_MEMBERSHIP_TABLE = "b2b_workspace_membership"
B2B_USER_TABLE = "b2b_user"
B2B_USER_SESSION_TABLE = "b2b_user_session"
B2B_DEPARTMENT_TABLE = "b2b_department"
B2B_EMPLOYEE_TABLE = "b2b_employee"
B2B_BUSINESS_TRIP_TABLE = "b2b_business_trip"
B2B_TRIP_EMPLOYEE_TABLE = "b2b_trip_employee"
B2B_TRAVEL_POLICY_TABLE = "b2b_travel_policy"
B2B_TRAVEL_POLICY_RULE_TABLE = "b2b_travel_policy_rule"
B2B_BUDGET_REQUEST_TABLE = "b2b_budget_request"
B2B_TRAVEL_VOUCHER_TABLE = "b2b_travel_voucher"
B2B_LEAD_REQUEST_TABLE = "b2b_lead_request"

# Mobile workspace (`/api/b2b/workspace/`)
B2B_TASK_TABLE = "b2b_task"
B2B_TASK_ASSIGNEE_TABLE = "b2b_task_assignee"
B2B_TASK_SUBTASK_TABLE = "b2b_task_subtask"
B2B_TASK_COMMENT_TABLE = "b2b_task_comment"
B2B_TASK_ACTIVITY_TABLE = "b2b_task_activity"
B2B_CALENDAR_EVENT_TABLE = "b2b_calendar_event"
B2B_CALENDAR_PARTICIPANT_TABLE = "b2b_calendar_participant"
# Which reminders have already gone out for an event, so a catch-up pass over
# the last few minutes cannot send the same one twice.
B2B_CALENDAR_REMINDER_TABLE = "b2b_calendar_reminder"
B2B_CHAT_THREAD_TABLE = "b2b_chat_thread"
B2B_CHAT_MEMBER_TABLE = "b2b_chat_member"
B2B_CHAT_MESSAGE_TABLE = "b2b_chat_message"
# One emoji from one person on one message — see `create_b2b_tables`.
B2B_CHAT_REACTION_TABLE = "b2b_chat_reaction"
B2B_WORKSPACE_LEAD_TABLE = "b2b_workspace_lead"
# The priced lines a lead is made of, and everything that has happened to it.
B2B_WORKSPACE_LEAD_ITEM_TABLE = "b2b_workspace_lead_item"
B2B_WORKSPACE_LEAD_ACTIVITY_TABLE = "b2b_workspace_lead_activity"
# The company's own customer directory. A lead is raised against one of these,
# so the second deal with the same buyer reuses the card rather than retyping it.
B2B_WORKSPACE_CUSTOMER_TABLE = "b2b_workspace_customer"
# Outside services plugged into the workspace. `b2b_integration` is the
# connection a workspace made — one row per (company, provider) — and
# `b2b_integration_page` is what that connection actually watches: a Facebook
# page or an Instagram account whose lead-ad forms land in the funnel.
B2B_INTEGRATION_TABLE = "b2b_integration"
B2B_INTEGRATION_PAGE_TABLE = "b2b_integration_page"
# Every leadgen Meta has handed us, whether or not it became a lead. This is
# what makes the webhook safe to retry: Meta redelivers, and a delivery that
# already has a row here is dropped instead of raising the same deal twice.
B2B_INTEGRATION_EVENT_TABLE = "b2b_integration_event"
B2B_WORKSPACE_FILE_TABLE = "b2b_workspace_file"
B2B_WORKSPACE_FOLDER_TABLE = "b2b_workspace_folder"
B2B_EMPLOYEE_OF_MONTH_TABLE = "b2b_employee_of_month"
B2B_ATTENDANCE_TABLE = "b2b_attendance"
B2B_ATTENDANCE_LOCATION_TABLE = "b2b_attendance_location"
B2B_SUPPORT_MESSAGE_TABLE = "b2b_support_message"

# Mail inside the workspace (`/api/b2b/workspace/mail/`).
#
# We do not host mail. An employee connects an inbox they already have — their
# Gmail, their company address, whatever — and it shows up in the chat section
# beside their colleagues. `b2b_mail_account` is that connection; the rest are
# the copy the apps read, because the phone and the dashboard talk to this API
# and never to IMAP.
B2B_MAIL_ACCOUNT_TABLE = "b2b_mail_account"
B2B_MAIL_THREAD_TABLE = "b2b_mail_thread"
B2B_MAIL_MESSAGE_TABLE = "b2b_mail_message"
B2B_MAIL_RECIPIENT_TABLE = "b2b_mail_recipient"
B2B_MAIL_ATTACHMENT_TABLE = "b2b_mail_attachment"
B2B_MAIL_OUTBOX_TABLE = "b2b_mail_outbox"

# In-app notification feed for workspace employees. The existing `notification`
# table only knows the `client` and `partner` roles, so B2B gets its own.
B2B_NOTIFICATION_TABLE = "b2b_notification"

# A request to hand over or close a Company — see `create_b2b_tables` and
# `access_repository.py`. Reviewed by WEEL staff in `admin_auth`, never
# actioned on the owner's own say-so.
B2B_OWNERSHIP_REQUEST_TABLE = "b2b_ownership_request"
