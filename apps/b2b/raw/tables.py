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
# One live video/audio call (Jitsi Meet) — who rang whom, from which module,
# and what came of it. The chat thread gets a system message off it; a lead or
# customer card lists it as history. See `apps/b2b/workspace/calls.py`.
B2B_CALL_TABLE = "b2b_call"
# One conference — a room many people are invited into at once, off the
# group thread that carries its invitation. A call rings one person; a
# conference is joined from a button. See `apps/b2b/workspace/conferences.py`.
B2B_CONFERENCE_TABLE = "b2b_conference"
B2B_WORKSPACE_LEAD_TABLE = "b2b_workspace_lead"
# The priced lines a lead is made of, and everything that has happened to it.
B2B_WORKSPACE_LEAD_ITEM_TABLE = "b2b_workspace_lead_item"
B2B_WORKSPACE_LEAD_ACTIVITY_TABLE = "b2b_workspace_lead_activity"
# The company's own customer directory. A lead is raised against one of these,
# so the second deal with the same buyer reuses the card rather than retyping it.
B2B_WORKSPACE_CUSTOMER_TABLE = "b2b_workspace_customer"
# Stock and catalogue behind the sales board — the Billz-style layer the
# dashboard's "Savdo" section manages. A `b2b_product` is what the company
# sells, a `b2b_warehouse` is where it keeps it, `b2b_stock` is how many of
# each sit in each, and `b2b_stock_movement` is the ledger every one of those
# quantities was derived from: a receipt, a write-off, a transfer, a count, or
# the sale a won lead turned into. See `apps/b2b/workspace/inventory_repository.py`.
B2B_WAREHOUSE_TABLE = "b2b_warehouse"
B2B_PRODUCT_CATEGORY_TABLE = "b2b_product_category"
B2B_PRODUCT_TABLE = "b2b_product"
B2B_STOCK_TABLE = "b2b_stock"
B2B_STOCK_MOVEMENT_TABLE = "b2b_stock_movement"
# The paper behind the ledger: a receipt, a transfer, a count, a write-off, a
# repricing, a sale, a return — each with its lines and a status, and the
# movements above point back at it. Confirming a document is what moves stock;
# cancelling one writes the reverse rather than deleting anything.
B2B_STOCK_DOCUMENT_TABLE = "b2b_stock_document"
B2B_STOCK_DOCUMENT_ITEM_TABLE = "b2b_stock_document_item"
B2B_SUPPLIER_TABLE = "b2b_supplier"
B2B_PRODUCT_COMPONENT_TABLE = "b2b_product_component"
B2B_PRICE_HISTORY_TABLE = "b2b_price_history"
B2B_INVENTORY_SETTINGS_TABLE = "b2b_inventory_settings"
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
# An AI assistant's history, as the workspace keeps it: a project (the
# vendor's folder of related chats with its own instructions), a chat, and the
# turns inside it. Filled from the vendor's export and by chats started here.
B2B_AI_PROJECT_TABLE = "b2b_ai_project"
B2B_AI_CONVERSATION_TABLE = "b2b_ai_conversation"
B2B_AI_MESSAGE_TABLE = "b2b_ai_message"
# A quick note pinned above the calendar — typed or recorded. Its own table
# rather than a flavour of `b2b_calendar_event`: a note has no time, and every
# query the calendar makes is a window over `starts_at`.
B2B_WORKSPACE_NOTE_TABLE = "b2b_workspace_note"
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

# Weel AI — the built-in analyst's reports, and who has read them.
B2B_AI_REPORT_TABLE = "b2b_ai_report"
B2B_AI_REPORT_SEEN_TABLE = "b2b_ai_report_seen"
