"""The Meta lead-ads integration, where it does not need a database.

Three things are worth pinning down and none of them is SQL:

  * **The mapping.** A lead-ad form is whatever the marketer drew, so
    `_map_fields` is the whole difference between a usable card and a row with
    a name in it. The cases below are the ones that actually turn up: Meta's
    own field names, a form written in Uzbek, a name split in two, and a form
    that asked things a lead has no column for.
  * **The signature.** The webhook puts rows on somebody's sales board and
    carries no login, so an unsigned or wrongly signed delivery must be
    dropped rather than logged and processed — before anything is looked up.
  * **Who may connect.** Everybody but a guest. Taking it away — unplugging,
    pausing a page, and the AI keys — stays with the owner, administrator and
    manager, plus whoever connected it.
  * **Whose pages.** Thousands of companies connect through Weel's one
    Facebook app. The callback attaches pages only to the company its `state`
    was issued to, and never takes a page another company already has. The
    SQL half of that rule is in `test_integrations_meta_db.py`.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.integrations import meta
from apps.b2b.integrations.credentials import MetaCredentials
from apps.b2b.integrations.ingest import _clean_phone, _map_fields
from apps.b2b.integrations.permissions import (
    may_connect_meta,
    may_manage_integrations,
    may_unplug_meta,
)
from apps.b2b.models import LeadSource
from apps.b2b.workspace.roles import capabilities_for


def field(name, *values):
    return {"name": name, "values": list(values)}


# ─── Mapping the form ─────────────────────────────────────────────────────────

def test_metas_own_field_names_land_in_the_right_columns():
    mapped = _map_fields([
        field("full_name", "Aziz Karimov"),
        field("phone_number", "+998 90 123 45 67"),
        field("email", "aziz@alfa.uz"),
        field("company_name", "Alfa Trade"),
        field("job_title", "Direktor"),
        field("city", "Toshkent"),
    ])
    assert mapped["full_name"] == "Aziz Karimov"
    assert mapped["phone"] == "+998901234567"
    assert mapped["email"] == "aziz@alfa.uz"
    assert mapped["company_name"] == "Alfa Trade"
    assert mapped["position"] == "Direktor"
    assert mapped["address"] == "Toshkent"
    assert mapped["extra"] == {}


def test_a_form_written_in_uzbek_still_maps():
    """The common case for this product: the marketer wrote their own
    questions and Meta slugged them."""
    mapped = _map_fields([
        field("Ismingiz", "Dilnoza"),
        field("Telefon raqamingiz", "998901112233"),
        field("Kompaniya nomi", "Beta MChJ"),
    ])
    assert mapped["full_name"] == "Dilnoza"
    assert mapped["phone"] == "998901112233"
    assert mapped["company_name"] == "Beta MChJ"


def test_a_split_name_is_joined():
    mapped = _map_fields([
        field("first_name", "Aziz"),
        field("last_name", "Karimov"),
        field("phone_number", "998901234567"),
    ])
    assert mapped["full_name"] == "Aziz Karimov"


def test_unmapped_answers_are_kept_rather_than_dropped():
    """Whatever else the form asked is the salesperson's best material. It has
    no column, so it goes in the bag — and from there into the lead's history."""
    mapped = _map_fields([
        field("full_name", "Aziz"),
        field("phone_number", "998901234567"),
        field("Byudjetingiz", "10 mln"),
        field("Qachon kerak", "Shu oyda"),
    ])
    assert mapped["extra"] == {"byudjetingiz": "10 mln", "qachon_kerak": "Shu oyda"}


def test_empty_answers_are_ignored():
    mapped = _map_fields([
        field("full_name", ""),
        field("phone_number", "998901234567"),
        field("email"),
    ])
    assert mapped["full_name"] == ""
    assert mapped["email"] == ""


def test_multiple_choice_answers_are_joined():
    mapped = _map_fields([field("qiziqish", "CRM", "Telefoniya")])
    assert mapped["product"] == "CRM, Telefoniya"


def test_phone_is_trimmed_to_the_column():
    assert _clean_phone("+998 (90) 123-45-67") == "+998901234567"
    assert len(_clean_phone("9" * 40)) == 20


# ─── The webhook's signature ──────────────────────────────────────────────────

def _signed(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correctly_signed_body_is_accepted():
    body = b'{"object":"page"}'
    assert meta.verify_signature(body, _signed(body), "app-secret")


def test_a_tampered_body_is_refused():
    assert not meta.verify_signature(
        b'{"object":"page","evil":1}', _signed(b'{"object":"page"}'), "app-secret"
    )


def test_an_unsigned_delivery_is_refused():
    assert not meta.verify_signature(b"{}", None, "app-secret")
    assert not meta.verify_signature(b"{}", "", "app-secret")
    assert not meta.verify_signature(b"{}", "sha1=abc", "app-secret")


def test_a_body_signed_with_another_secret_is_refused():
    """Signed, but not by Weel's app — a leaked page id is not enough to put
    leads on anybody's board."""
    body = b'{"object":"page","entry":[]}'
    assert meta.verify_signature(body, _signed(body, "alfa-secret"), "alfa-secret")
    assert not meta.verify_signature(body, _signed(body, "alfa-secret"), "beta-secret")


def test_a_server_with_no_app_secret_verifies_nothing():
    """An unconfigured server must refuse rather than accept everything —
    an empty secret is not a wildcard."""
    body = b"{}"
    assert not meta.verify_signature(body, _signed(body, ""), "")


# ─── Who may connect it ───────────────────────────────────────────────────────

def test_everybody_but_a_guest_may_connect_meta():
    # The owner's call, 2026-09-11: owner, manager, lider and employee.
    # "lider" and "performer" are the roster's older words for the
    # administrator and the manager.
    for role in ("owner", "admin", "lider", "manager", "performer", "employee"):
        assert may_connect_meta(role), role
    assert not may_connect_meta("guest")
    # An unreadable role resolves to `employee`, never to more.
    assert may_connect_meta(None) is may_connect_meta("employee")


def test_the_managing_roles_alone_manage_the_rest():
    """The AI keys, and unplugging somebody else's Meta connection."""
    for role in ("owner", "lider", "admin", "manager", "performer"):
        assert may_manage_integrations(role), role
    assert not may_manage_integrations("employee")
    assert not may_manage_integrations("guest")
    assert not may_manage_integrations(None)
    assert not may_manage_integrations("wat")


class _Viewer:
    def __init__(self, id, role):
        self.id, self.role = id, role


def test_whoever_connected_meta_may_unplug_it_and_nobody_else_below_a_manager():
    integration = {"id": 1, "connected_by_id": 7}
    assert may_unplug_meta(_Viewer(7, "employee"), integration)
    assert not may_unplug_meta(_Viewer(8, "employee"), integration)
    assert may_unplug_meta(_Viewer(8, "manager"), integration)
    assert may_unplug_meta(_Viewer(8, "owner"), integration)
    # A guest who once connected it (as an employee, since demoted) does not
    # keep the switch.
    assert not may_unplug_meta(_Viewer(7, "guest"), integration)
    assert not may_unplug_meta(_Viewer(7, "employee"), None)
    assert not may_unplug_meta(_Viewer(7, "employee"), {"connected_by_id": None})


def test_the_capability_the_app_draws_its_row_from_agrees():
    assert capabilities_for("owner")["can_manage_integrations"]
    assert capabilities_for("lider")["can_manage_integrations"]
    assert capabilities_for("performer")["can_manage_integrations"]
    assert capabilities_for("employee")["can_manage_integrations"]
    assert not capabilities_for("guest")["can_manage_integrations"]


# ─── One app, many companies ──────────────────────────────────────────────────

def _creds(app_id="1", secret="s", uri="https://x/callback/"):
    return MetaCredentials(
        app_id=app_id, app_secret=secret, redirect_uri=uri, verify_token="tok",
    )


def test_a_credential_set_is_complete_only_with_all_three():
    assert _creds().is_complete
    assert not _creds(app_id="").is_complete
    assert not _creds(secret="").is_complete
    assert not _creds(uri="").is_complete


def test_nobody_is_asked_for_an_app_id_a_secret_or_a_token():
    """Connecting is signing in to Facebook, nothing else. The route that took
    a workspace's own App ID and App Secret is gone, and the connection's row
    carries no values to paste anywhere — `setup` stays in the shape for the
    builds already installed, and is always null."""
    from django.urls import NoReverseMatch, reverse

    from apps.b2b.integrations import serializers, views

    try:
        reverse("ws-integration-meta-app")
    except NoReverseMatch:
        pass
    else:  # pragma: no cover
        raise AssertionError("the own-app route is still mounted")
    assert not hasattr(serializers, "MetaAppSerializer")

    with patch.object(views.int_repo, "get_integration", return_value=None), \
         patch.object(views.credentials, "is_available", return_value=True):
        payload = views.meta_payload(10)
    assert payload["setup"] is None
    assert payload["available"] is True


def _callback(query, *, state_holder, pages, held_elsewhere=(), upsert=None,
              existing=None, account=None):
    """Run the OAuth callback with Meta, the cache and the database faked.

    Answers the response and the calls the repository saw, so a test can say
    what was stored for whom.
    """
    from rest_framework.test import APIRequestFactory

    from apps.b2b.integrations import public_views

    stored = []

    def fake_upsert_page(**kwargs):
        stored.append(kwargs)
        return upsert(kwargs) if upsert else {"id": len(stored), **kwargs}

    cache = {}
    if state_holder is not None:
        cache[public_views.state_key("st")] = state_holder

    class FakeCache:
        def get(self, key):
            return cache.get(key)

        def delete(self, key):
            return cache.pop(key, None) is not None

    request = APIRequestFactory().get("/api/b2b/integrations/meta/callback/", query)
    with patch.object(public_views, "cache", FakeCache()), \
         patch.object(public_views.credentials, "global_credentials", return_value=_creds()), \
         patch.object(public_views.meta, "exchange_code", return_value={"access_token": "s"}), \
         patch.object(public_views.meta, "long_lived_token", return_value={"access_token": "l"}), \
         patch.object(public_views.meta, "me",
                      return_value=account or {"id": "u1", "name": "Aziz"}), \
         patch.object(public_views.meta, "list_pages", return_value=pages), \
         patch.object(public_views.meta, "subscribe_page") as subscribe, \
         patch.object(public_views.crypto, "encrypt", side_effect=lambda v: f"enc:{v}"), \
         patch.object(public_views.int_repo, "get_integration", return_value=existing), \
         patch.object(public_views.int_repo, "upsert_integration",
                      side_effect=lambda **kw: {"id": 500 + kw["company_id"], **kw}) as upsert_integration, \
         patch.object(public_views.int_repo, "page_held_elsewhere",
                      side_effect=lambda page_id, company_id: page_id in held_elsewhere), \
         patch.object(public_views.int_repo, "upsert_page", side_effect=fake_upsert_page), \
         patch.object(public_views.int_repo, "delete_pages_except") as delete_except, \
         patch.object(public_views.int_repo, "set_integration_status") as set_status, \
         patch("apps.b2b.integrations.tasks.sync_meta_pages.delay"):
        response = public_views.MetaOAuthCallbackView.as_view()(request)
        response.render() if hasattr(response, "render") else None
    return {
        "response": response,
        "html": response.content.decode(),
        "stored": stored,
        "subscribed": [c.args[0] for c in subscribe.call_args_list],
        "integration": upsert_integration.call_args,
        "delete_except": delete_except.call_args,
        "status": set_status.call_args,
    }


def _page(page_id, name):
    return {"id": page_id, "name": name, "access_token": f"tok-{page_id}"}


def test_the_pages_go_to_the_company_the_state_was_issued_to():
    """Nothing in the callback URL can name a company — only our own state."""
    run = _callback(
        {"code": "c", "state": "st", "company_id": "999"},
        state_holder={"company_id": 10, "employee_id": 7},
        pages=[_page("P1", "Alfa do‘koni")],
    )
    assert "Meta ulandi" in run["html"]
    assert run["integration"].kwargs["company_id"] == 10
    assert [p["company_id"] for p in run["stored"]] == [10]
    assert run["subscribed"] == ["P1"]


def test_a_page_another_company_has_is_left_with_it():
    """One marketer administers pages for several clients and connects in one
    client's workspace: Facebook hands us every page they can manage. The one
    already connected by another company stays there — not subscribed again,
    not re-pointed — and the owner is told why it is missing."""
    run = _callback(
        {"code": "c", "state": "st"},
        state_holder={"company_id": 10, "employee_id": 7},
        pages=[_page("P1", "Alfa do‘koni"), _page("P2", "Beta klinikasi")],
        held_elsewhere={"P2"},
    )
    assert [p["page_id"] for p in run["stored"]] == ["P1"]
    assert run["subscribed"] == ["P1"]
    assert "Beta klinikasi" in run["html"]
    assert run["delete_except"].args == (510, ["P1"])
    # Connected, with the note, rather than an error: P1 works.
    assert run["status"].args[1] == "connected"
    assert "Beta klinikasi" in run["status"].kwargs["error"]


def test_losing_the_race_for_a_page_is_reported_the_same_way():
    """Two companies connecting one page in the same second: the SQL guard
    answers None to the loser, and the loser does not count it as theirs."""
    run = _callback(
        {"code": "c", "state": "st"},
        state_holder={"company_id": 10, "employee_id": 7},
        pages=[_page("P1", "Alfa do‘koni")],
        upsert=lambda kwargs: None,
    )
    assert "Sahifa ulanmadi" in run["html"]
    assert "Alfa do‘koni" in run["html"]
    assert run["delete_except"].args == (510, [])
    assert run["status"].args[1] == "error"


_LIVE = {"id": 510, "company_id": 10, "account_id": "u1", "access_token_enc": "enc:l",
         "connected_by_id": 7}


def test_a_second_account_adds_its_pages_and_takes_nothing_away():
    """An employee signs in with their own Facebook to a company the owner
    has already connected. Their page joins the owner's connection; the
    owner's pages, account and token stay exactly as they were."""
    run = _callback(
        {"code": "c", "state": "st"},
        state_holder={"company_id": 10, "employee_id": 9},
        pages=[_page("P9", "Filial sahifasi")],
        existing=_LIVE,
        account={"id": "u9", "name": "Dilshod"},
    )
    assert "1 ta sahifa qo‘shildi" in run["html"]
    assert run["integration"] is None  # the connection was not taken over
    assert run["delete_except"] is None  # and nothing was dropped
    assert run["status"] is None  # nor was its status touched
    assert [(p["integration_id"], p["page_id"]) for p in run["stored"]] == [(510, "P9")]
    assert run["subscribed"] == ["P9"]


def test_a_login_with_no_usable_page_leaves_a_working_connection_alone():
    """Before this, a Facebook account administering no page (or only other
    companies') reconnected the company with an empty list and wiped every
    page it had."""
    for account, pages, held in (
        ({"id": "u9", "name": "Dilshod"}, [], ()),
        ({"id": "u1", "name": "Aziz"}, [], ()),
        ({"id": "u9", "name": "Dilshod"}, [_page("P2", "Beta klinikasi")], {"P2"}),
    ):
        run = _callback(
            {"code": "c", "state": "st"},
            state_holder={"company_id": 10, "employee_id": 9},
            pages=pages, held_elsewhere=held, existing=_LIVE, account=account,
        )
        assert "Sahifa ulanmadi" in run["html"]
        assert run["integration"] is None
        assert run["delete_except"] is None
        assert run["status"] is None
        assert run["stored"] == []


def test_the_same_account_reconnecting_rewrites_its_list():
    run = _callback(
        {"code": "c", "state": "st"},
        state_holder={"company_id": 10, "employee_id": 7},
        pages=[_page("P1", "Alfa do‘koni")],
        existing=_LIVE,
    )
    assert "1 ta sahifa ulandi" in run["html"]
    assert run["integration"].kwargs["account_id"] == "u1"
    assert run["delete_except"].args == (510, ["P1"])


def test_a_state_is_good_for_one_callback_only():
    run = _callback(
        {"code": "c", "state": "st"}, state_holder=None, pages=[_page("P1", "A")],
    )
    assert "Muddati tugadi" in run["html"]
    assert run["stored"] == []


def test_what_the_callback_prints_is_escaped():
    """`error` comes straight off the query string, and the page is served from
    our API domain — echoing it raw would be a reflected XSS."""
    run = _callback(
        {"error": "<script>alert(1)</script>"}, state_holder=None, pages=[],
    )
    assert "<script>" not in run["html"]
    assert "&lt;script&gt;" in run["html"]


def _webhook(body: bytes, signature: str | None, *, page=None):
    from rest_framework.test import APIRequestFactory

    from apps.b2b.integrations import public_views

    headers = {"HTTP_X_HUB_SIGNATURE_256": signature} if signature else {}
    request = APIRequestFactory().post(
        "/api/b2b/integrations/meta/webhook/", body,
        content_type="application/json", **headers,
    )
    with patch.object(public_views.credentials, "global_credentials",
                      return_value=_creds(secret="app-secret")), \
         patch.object(public_views.int_repo, "find_page", return_value=page) as find_page, \
         patch.object(public_views.int_repo, "claim_event",
                      return_value={"id": 1}) as claim, \
         patch.object(public_views.ingest_meta_lead, "delay") as delay:
        response = public_views.MetaWebhookView.as_view()(request)
    return response, find_page, claim, delay


_DELIVERY = json.dumps({"object": "page", "entry": [{"changes": [{
    "field": "leadgen",
    "value": {"leadgen_id": "L1", "page_id": "P1", "form_id": "F1"},
}]}]}).encode()


def test_an_unsigned_delivery_is_refused_before_anything_is_looked_up():
    response, find_page, claim, delay = _webhook(_DELIVERY, None)
    assert response.status_code == 403
    find_page.assert_not_called()
    delay.assert_not_called()


def test_a_signed_delivery_lands_with_the_page_s_company_only():
    page = {"id": 3, "page_id": "P1", "company_id": 10, "is_active": True}
    response, _, claim, delay = _webhook(
        _DELIVERY, _signed(_DELIVERY), page=page,
    )
    assert response.status_code == 200
    assert claim.call_args.kwargs["company_id"] == 10
    delay.assert_called_once_with(3, "L1", "F1", 1)


def test_the_handshake_answers_only_weel_s_own_verify_token():
    from rest_framework.test import APIRequestFactory

    from apps.b2b.integrations import public_views

    def ask(token):
        request = APIRequestFactory().get(
            "/api/b2b/integrations/meta/webhook/",
            {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"},
        )
        with patch.object(public_views.credentials, "global_credentials",
                          return_value=_creds()):
            return public_views.MetaWebhookView.as_view()(request)

    assert ask("tok").content == b"42"
    assert ask("guess").status_code == 403


# ─── Nobody may claim a lead came from Meta ───────────────────────────────────

def test_meta_is_not_a_source_a_person_can_pick():
    """`source` is what the funnel reports a channel by. A badge that anybody
    could type is not evidence of anything, so `meta` is written only by the
    ingest path."""
    assert LeadSource.META in LeadSource.CHOICES
    assert LeadSource.META not in LeadSource.MANUAL_CHOICES


# ─── The screen, as each role gets it ─────────────────────────────────────────

def _as(role, *, employee_id=8, method="get", view=None, integration=None, **kwargs):
    """Call an integrations view as `role`, with the database faked."""
    from rest_framework.test import APIRequestFactory, force_authenticate

    from apps.b2b.integrations import views
    from apps.b2b.workspace.authentication import WorkspaceUser

    user = WorkspaceUser({"id": employee_id, "company_id": 10, "role": role})
    request = getattr(APIRequestFactory(), method)("/x/", format="json")
    force_authenticate(request, user=user)
    with patch.object(views.int_repo, "get_integration", return_value=integration), \
         patch.object(views.int_repo, "list_pages", return_value=[]), \
         patch.object(views.int_repo, "delete_pages") as delete_pages, \
         patch.object(views.int_repo, "disconnect") as disconnect, \
         patch.object(views.b2b_repo, "get_employee", return_value=None), \
         patch.object(views.credentials, "is_available", return_value=True), \
         patch("apps.b2b.integrations.ai_views.ai_payload",
               side_effect=lambda company_id, provider: {"provider": provider}):
        response = view.as_view()(request, **kwargs)
    return response, disconnect


def test_an_employee_s_screen_is_meta_alone():
    from apps.b2b.integrations import views

    response, _ = _as("employee", view=views.IntegrationListView)
    assert response.status_code == 200
    assert [row["provider"] for row in response.data["results"]] == ["meta"]
    assert response.data["can_manage"] is False

    response, _ = _as("manager", view=views.IntegrationListView)
    assert len(response.data["results"]) > 1
    assert response.data["can_manage"] is True


def test_a_guest_is_refused_the_screen():
    from apps.b2b.integrations import views

    response, _ = _as("guest", view=views.IntegrationListView)
    assert response.status_code == 403


def test_an_employee_cannot_unplug_the_owner_s_connection():
    from apps.b2b.integrations import views

    response, disconnect = _as(
        "employee", method="delete", view=views.MetaDisconnectView,
        integration=_LIVE,  # connected by employee 7
    )
    assert response.status_code == 403
    disconnect.assert_not_called()

    response, disconnect = _as(
        "employee", employee_id=7, method="delete", view=views.MetaDisconnectView,
        integration=_LIVE,
    )
    assert response.status_code == 200
    disconnect.assert_called_once()


def test_the_row_says_who_may_disconnect():
    from apps.b2b.integrations import views

    response, _ = _as("employee", view=views.MetaDisconnectView, integration=_LIVE)
    assert response.data["can_disconnect"] is False
    response, _ = _as("employee", employee_id=7, view=views.MetaDisconnectView,
                      integration=_LIVE)
    assert response.data["can_disconnect"] is True
    response, _ = _as("lider", view=views.MetaDisconnectView, integration=_LIVE)
    assert response.data["can_disconnect"] is True
