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


# Somebody from another workspace is being asked to come and help.
SECONDMENT_TITLE = "Yangi so’rov"

# The answer, back to whoever asked.
SECONDMENT_ACCEPTED_TITLE = "So’rov qabul qilindi"
SECONDMENT_DECLINED_TITLE = "So’rov rad etildi"

# Somebody asked to join the workspace. Addressed to whoever may decide it —
# owner, admin, or anyone else the workspace has handed `employees.invite`.
JOIN_REQUEST_TITLE = "Yangi so’rov"


def join_request_body(full_name: str, message: str | None = None) -> str:
    text = f"{full_name} jamoaga qo’shilishni so’ramoqda"
    message = (message or "").strip()
    return f"{text}: {message}" if message else text


# Somebody asked to join a workspace, and it has been answered. Addressed to
# the asker, who is not in the workspace yet and has no other way of finding
# out — the screen they are looking at has nothing on it but their own
# request.
JOIN_ACCEPTED_TITLE = "So’rovingiz qabul qilindi"
JOIN_DECLINED_TITLE = "So’rovingiz rad etildi"


def join_accepted_body(company_name: str) -> str:
    return f"«{company_name}» jamoasiga qo’shildingiz. Ilovaga kiring."


def join_declined_body(company_name: str, reason: str | None = None) -> str:
    """Turned down, and why when the workspace said why.

    The reason is carried through rather than softened away: somebody who is
    told only "rad etildi" asks again, and the second request is refused for
    the same unstated reason as the first.
    """
    text = f"«{company_name}» jamoasi so’rovingizni rad etdi."
    reason = (reason or "").strip()
    return f"{text} Sabab: {reason}" if reason else text
