"""Mail inside the workspace chat section.

We do not host mail. An employee connects an inbox they already have —
their Gmail, their company address, whatever — and it shows up beside their
colleagues' chats, on the phone and on the dashboard. This package is a client
of whatever provider they connect, in three directions:

* ``connection``  — opens an authenticated IMAP or SMTP session, whether the
  account was connected with an app password or Google sign-in.
* ``imap_sync``   — pulls new mail into ``b2b_mail_*`` so both apps can read it
  from this API instead of speaking IMAP themselves.
* ``smtp_send``   — submits outgoing mail through the account's own provider,
  as the account, so it carries that provider's own reputation and DKIM.

Nothing in here talks to Django's ``django.core.mail``: that is configured
nowhere in this project, and these messages are per-employee's own inbox
rather than per-application anyway.
"""
