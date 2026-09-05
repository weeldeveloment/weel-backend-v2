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

# A lead that arrived by itself, from a connected Facebook/Instagram form.
# Said differently from LEAD_TITLE on purpose: a manager posting a lead and a
# stranger filling in an ad form are two different things to walk into, and
# the second one is nobody's yet.
META_LEAD_TITLE = "Meta’dan yangi lead"

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


# ─── Jonli qo'ng'iroq ─────────────────────────────────────────────────────────

# Somebody is ringing. The body names them and says whether it is video.
CALL_INCOMING_TITLE = "Kiruvchi qo’ng’iroq"
# They rang and nobody picked up.
CALL_MISSED_TITLE = "Javobsiz qo’ng’iroq"

_CALL_KIND = {"video": "Video qo’ng’iroq", "audio": "Audio qo’ng’iroq"}

#: What a conference is called wherever it has to be named on its own — the
#: push title, and the fallback when somebody opens one without typing a name.
CONFERENCE_TITLE = "Konferensiya"


def conference_invite_body(organiser_name: str, title: str) -> str:
    """"Aziz Karimov · Haftalik yig'ilish" — who called it and what it is
    about, in the order a reader scans them."""
    name = (organiser_name or "").strip() or "Weel"
    subject = (title or "").strip()
    return f"{name} · {subject}" if subject else name


def call_kind_label(call_type: str) -> str:
    return _CALL_KIND.get(call_type, _CALL_KIND["video"])


def call_incoming_body(caller_name: str, call_type: str) -> str:
    return f"{caller_name} · {call_kind_label(call_type)}"


def call_missed_body(caller_name: str, call_type: str) -> str:
    return f"{caller_name} · {call_kind_label(call_type)}"


def format_call_duration(seconds: int) -> str:
    """4:12, or 1:04:12 past the hour — what the chat line and the lead
    card print after a finished call."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def call_log_label(call_type: str, status: str, seconds: int) -> str:
    """The human line of a call's chat message — TZ §4.1.4."""
    kind = call_kind_label(call_type)
    if status == "ended":
        return f"{kind} · {format_call_duration(seconds)}"
    if status == "declined":
        return f"{kind} · rad etildi"
    if status in ("missed", "cancelled"):
        return CALL_MISSED_TITLE
    if status == "failed":
        return f"{kind} · ulanmadi"
    return kind


def call_guest_sms(company_name: str, link: str) -> str:
    """The SMS a lead who is not in Weel receives. Short: an SMS is priced by
    the 70 characters, and a link is most of one already."""
    who = company_name.strip() or "Weel"
    return f"{who} sizni video suhbatga taklif qilmoqda. Havola: {link}"


# Weel AI — the built-in analyst's report is ready. One title per period,
# because "Hisobot tayyor" alone does not say whether it is yesterday or the
# year, and the owner opens the two with different amounts of time in hand.
ANALYST_TITLES = {
    "day": "Weel AI: kunlik tahlil",
    "week": "Weel AI: haftalik tahlil",
    "month": "Weel AI: oylik tahlil",
    "year": "Weel AI: yillik tahlil",
}


def analyst_title(period: str) -> str:
    return ANALYST_TITLES.get(period, "Weel AI: tahlil")
