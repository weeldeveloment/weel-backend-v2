"""Corporate mail for B2B companies.

A company brings a domain it owns (``kompaniya.com``), publishes the DNS
records we hand it, and from then on its employees have real mailboxes —
``aziz@kompaniya.com`` — that can exchange mail with gmail.com and anything
else on the internet.

The mail server itself is Mailcow, running on its own host. This package is a
client of it in three directions:

* ``mailcow``    — the admin API, used once per domain and once per mailbox.
* ``smtp_send``  — submission, authenticating *as the sending mailbox* so that
  SPF and DKIM align on the employee's own address.
* ``imap_sync``  — pulls new mail into ``b2b_mail_*`` so the web dashboard and
  the phone can read it from this API instead of speaking IMAP themselves.

Nothing in here talks to Django's ``django.core.mail``: that is configured
nowhere in this project, and these messages are per-user rather than
per-application anyway.
"""
