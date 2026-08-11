B2B_COMPANY_TABLE = "b2b_company"
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
B2B_HOTEL_BOOKING_REQUEST_TABLE = "b2b_hotel_booking_request"
B2B_HOTEL_BOOKING_ROOM_TABLE = "b2b_hotel_booking_room"
B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE = "b2b_hotel_booking_room_employee"
B2B_LEAD_REQUEST_TABLE = "b2b_lead_request"

# Mobile workspace (`/api/b2b/workspace/`)
B2B_TASK_TABLE = "b2b_task"
B2B_TASK_ASSIGNEE_TABLE = "b2b_task_assignee"
B2B_TASK_SUBTASK_TABLE = "b2b_task_subtask"
B2B_TASK_COMMENT_TABLE = "b2b_task_comment"
B2B_CALENDAR_EVENT_TABLE = "b2b_calendar_event"
B2B_CALENDAR_PARTICIPANT_TABLE = "b2b_calendar_participant"
B2B_CHAT_THREAD_TABLE = "b2b_chat_thread"
B2B_CHAT_MEMBER_TABLE = "b2b_chat_member"
B2B_CHAT_MESSAGE_TABLE = "b2b_chat_message"
B2B_WORKSPACE_LEAD_TABLE = "b2b_workspace_lead"
B2B_WORKSPACE_FILE_TABLE = "b2b_workspace_file"
B2B_EMPLOYEE_OF_MONTH_TABLE = "b2b_employee_of_month"

# Corporate mail (`/api/b2b/workspace/mail/`).
#
# A company brings its own domain (`kompaniya.com`); every employee who is given
# a mailbox gets `local_part@that domain`. The mail itself lives on the Mailcow
# server — these tables are the copy the apps read, because the phone and the
# dashboard talk to this API, never to IMAP.
B2B_MAIL_DOMAIN_TABLE = "b2b_mail_domain"
B2B_MAILBOX_TABLE = "b2b_mailbox"
B2B_MAIL_THREAD_TABLE = "b2b_mail_thread"
B2B_MAIL_MESSAGE_TABLE = "b2b_mail_message"
B2B_MAIL_RECIPIENT_TABLE = "b2b_mail_recipient"
B2B_MAIL_ATTACHMENT_TABLE = "b2b_mail_attachment"
B2B_MAIL_OUTBOX_TABLE = "b2b_mail_outbox"

# In-app notification feed for workspace employees. The existing `notification`
# table only knows the `client` and `partner` roles, so B2B gets its own.
B2B_NOTIFICATION_TABLE = "b2b_notification"
