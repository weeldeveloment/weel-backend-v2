"""What a workspace notification says, in Uzbek.

Plain strings rather than `gettext`, deliberately.

A push is read by somebody who is not making a request — often hours later,
on a phone that is asleep. There is no request locale to take a language from,
and taking one from the *sender's* request is worse than useless: it would
have a manager's browser decide what language their colleague's phone speaks.
Django's active language in a Celery worker is `settings.LANGUAGE_CODE`, which
is `"en"`, so every one of these came out in English — `_("New lead")` reached
the phone as "New lead" rather than "Yangi lead", and the strings were not in
`locale/uz/LC_MESSAGES/django.po` to be translated in the first place.

The workspace app is Uzbek throughout — its tabs, its buttons, its empty
states are all written this way — so these follow it. If the app ever needs a
second language, the language belongs on the employee record, and this module
is the one place that has to learn to read it.
"""
from __future__ import annotations

# A lead left on the board for anyone to claim.
LEAD_TITLE = "Yangi lead"

# A task somebody was put on.
TASK_TITLE = "Yangi vazifa"

# A calendar entry somebody was invited to.
EVENT_TITLE = "Yangi tadbir"

# The event is starting now — the same warning, in the present tense.
EVENT_STARTING_TITLE = "Tadbir boshlanmoqda"


def event_reminder_title(minutes_before: int) -> str:
    """The heading on one of the three warnings before an event.

    `0` is the event starting rather than "in 0 minutes", which is why this is
    a function and not a dict.
    """
    if minutes_before <= 0:
        return EVENT_STARTING_TITLE
    return f"{minutes_before} daqiqadan keyin"
