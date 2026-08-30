"""The Meta lead-ads integration, where it does not need a database.

Three things are worth pinning down and none of them is SQL:

  * **The mapping.** A lead-ad form is whatever the marketer drew, so
    `_map_fields` is the whole difference between a usable card and a row with
    a name in it. The cases below are the ones that actually turn up: Meta's
    own field names, a form written in Uzbek, a name split in two, and a form
    that asked things a lead has no column for.
  * **The signature.** The webhook puts rows on somebody's sales board and
    carries no login, so an unsigned or wrongly signed delivery must be
    dropped rather than logged and processed. One URL now receives deliveries
    from several Facebook apps — ours and every workspace that brought its
    own — so the check has to be against the *right* secret, and a body signed
    by one company's app must not pass for another's.
  * **Who may connect.** Owner and administrator only — not a manager, and not
    an employee.
"""
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.integrations import meta
from apps.b2b.integrations.credentials import MetaCredentials
from apps.b2b.integrations.ingest import _clean_phone, _map_fields
from apps.b2b.integrations.permissions import may_manage_integrations
from apps.b2b.models import LeadSource
from apps.b2b.workspace.access import capabilities_for


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
    import hashlib
    import hmac

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


def test_one_company_s_app_cannot_sign_for_another_s():
    """The reason the check moved off the settings.

    Two workspaces on their own Facebook apps post to the same webhook URL. A
    delivery signed by one must not be accepted as the other's, or a customer
    could raise leads on a competitor's board by learning their page id.
    """
    body = b'{"object":"page","entry":[]}'
    assert meta.verify_signature(body, _signed(body, "alfa-secret"), "alfa-secret")
    assert not meta.verify_signature(body, _signed(body, "alfa-secret"), "beta-secret")


def test_a_company_with_no_app_secret_verifies_nothing():
    """An unconfigured company must refuse rather than accept everything —
    an empty secret is not a wildcard."""
    body = b"{}"
    assert not meta.verify_signature(body, _signed(body, ""), "")


# ─── Who may connect it ───────────────────────────────────────────────────────

def test_only_the_owner_and_the_administrator_may_manage_integrations():
    assert may_manage_integrations("owner")
    # "lider" is the roster's older word for the workspace administrator.
    assert may_manage_integrations("lider")
    assert may_manage_integrations("admin")
    assert not may_manage_integrations("performer")   # manager
    assert not may_manage_integrations("manager")
    assert not may_manage_integrations("employee")
    assert not may_manage_integrations(None)


def test_the_capability_the_app_draws_its_row_from_agrees():
    assert capabilities_for("owner")["can_manage_integrations"]
    assert capabilities_for("lider")["can_manage_integrations"]
    assert not capabilities_for("performer")["can_manage_integrations"]
    assert not capabilities_for("employee")["can_manage_integrations"]


# ─── Whose Facebook app ───────────────────────────────────────────────────────

def _creds(app_id="1", secret="s", uri="https://x/callback/", own=False):
    return MetaCredentials(
        app_id=app_id, app_secret=secret, redirect_uri=uri,
        verify_token="tok", is_own=own,
    )


def test_a_credential_set_is_complete_only_with_all_three():
    assert _creds().is_complete
    assert not _creds(app_id="").is_complete
    assert not _creds(secret="").is_complete
    assert not _creds(uri="").is_complete


def test_the_workspace_s_own_app_wins_over_the_deployment_s():
    """The whole point of the second path: a company that cannot use ours —
    because ours is still in Meta's review, or because their policy forbids
    it — connects through an app they own, and nothing downstream notices."""
    from apps.b2b.integrations import credentials

    with patch.object(credentials.crypto, "decrypt", return_value="their-secret"), \
         patch.object(credentials, "_redirect_uri", return_value="https://x/cb/"):
        own = credentials.from_integration(
            {"app_id": "999", "app_secret_enc": "***", "webhook_verify_token": "t"}
        )

    assert own is not None
    assert own.is_own
    assert own.app_id == "999"
    assert own.app_secret == "their-secret"


def test_a_half_saved_app_falls_back_rather_than_breaking():
    """An id with no secret is not a narrower configuration, it is none. It
    must not stop the leads arriving while somebody works out which half is
    missing."""
    from apps.b2b.integrations import credentials

    assert credentials.from_integration({"app_id": "999"}) is None
    assert credentials.from_integration({"app_secret_enc": "***"}) is None
    assert credentials.from_integration(None) is None


def test_a_secret_that_cannot_be_decrypted_is_not_silently_replaced_by_ours():
    """Falling back here would connect them through the wrong Facebook app
    without saying so. Answering "no app" makes the screen ask again."""
    from apps.b2b.integrations import credentials

    with patch.object(credentials.crypto, "decrypt", side_effect=ValueError("key")):
        assert credentials.from_integration(
            {"app_id": "999", "app_secret_enc": "***"}
        ) is None


def test_a_generated_verify_token_is_not_something_a_person_would_pick():
    from apps.b2b.integrations import credentials

    token = credentials.new_verify_token()
    assert len(token) >= 24
    assert token != credentials.new_verify_token()


# ─── Nobody may claim a lead came from Meta ───────────────────────────────────

def test_meta_is_not_a_source_a_person_can_pick():
    """`source` is what the funnel reports a channel by. A badge that anybody
    could type is not evidence of anything, so `meta` is written only by the
    ingest path."""
    assert LeadSource.META in LeadSource.CHOICES
    assert LeadSource.META not in LeadSource.MANUAL_CHOICES
