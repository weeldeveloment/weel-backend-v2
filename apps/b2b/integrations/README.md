# Meta (Facebook / Instagram) lead ads

A company connects its own Facebook pages; the lead-ad forms on them fill the
sales funnel. Every lead that arrives this way is marked `source = meta` and
shows a "Meta’dan" badge in the app.

Only the workspace **owner** and **administrator** ("lider" in the roster's
vocabulary) can connect or disconnect it. That is enforced on the server —
`apps/b2b/integrations/permissions.py` — and reported to the app as the
`can_manage_integrations` capability so the profile row and the endpoint agree.

## The flow, end to end

1. The owner opens **Profil → Integratsiya → Meta** and taps "Ulash".
   `POST /api/b2b/workspace/integrations/meta/connect/` answers with an
   `authorize_url`; the phone opens it in its browser.
2. They sign in to Facebook and grant the four scopes.
3. Meta redirects the browser to
   `GET /api/b2b/integrations/meta/callback/`. That endpoint exchanges the
   code for a long-lived (~60 day) user token, reads every page the person
   administers, stores each page's own token, and subscribes each page to the
   `leadgen` webhook. It then shows an "Ilovaga qayting" page.
4. The app polls `GET /api/b2b/workspace/integrations/` when it comes back to
   the foreground and shows the connected pages.
5. A customer submits a form. Meta posts
   `POST /api/b2b/integrations/meta/webhook/`; the delivery is logged, a
   Celery task fetches the answers and raises an **unclaimed** lead on the
   board. Everybody in the workspace is notified, and the first to take it
   owns it.

## One app, or one per company

There are two models and the product supports both. Which one a workspace uses
is decided in exactly one place — `credentials.for_company` — and nothing
downstream knows the difference.

### The deployment's app (the default)

`META_APP_ID` / `META_APP_SECRET` in the settings are **Weel's own** Facebook
app. One app serves every customer:

```
settings          b2b_integration
──────────        ─────────────────────────────────────
META_APP_ID=123   company_id=10   token=***   (Alfa Trade)
  (one, ours)     company_id=11   token=***   (Beta MChJ)
                  company_id=12   token=***   (Vega)
                  …one row per company, encrypted
```

Nothing goes in the `.env` per customer. A company signs in with *their*
Facebook account, and their token is stored against their own `company_id`.
This is the same shape as the Gmail connection already in this codebase
(`B2B_MAIL_GOOGLE_CLIENT_ID` is one value; each employee's refresh token lives
in `b2b_mail_account`).

### The workspace's own app

A company can instead connect through a Facebook app **they** own. Two reasons
this is not optional:

* Until Meta approves our app, `leads_retrieval` works only for accounts
  listed on it as testers. A customer with their own approved app is not
  blocked by our review.
* Some customers will not let advertising data pass through an app they do not
  control.

The owner enters their App ID and App Secret in the app
(**Profil → Integratsiya → Meta → O'z ilovangiz**). It is stored encrypted on
`b2b_integration` and wins over the settings for that company alone:

```
b2b_integration
  company_id=10   app_id=555   app_secret=***   token=***   ← their app
  company_id=11   app_id=777   app_secret=***   token=***   ← their app
  company_id=12   app_id=NULL                   token=***   ← ours
```

A deployment where every customer brings their own can leave `META_APP_ID` and
`META_APP_SECRET` unset and keep `META_INTEGRATION_ENABLED=true`.

**What differs when a workspace uses its own app**

| | Deployment's app | Workspace's app |
|---|---|---|
| Redirect URI | one, in `META_REDIRECT_URI` | the same URL, registered in *their* app |
| Webhook URL | one | the same URL |
| Verify token | `META_WEBHOOK_VERIFY_TOKEN` | generated per workspace |
| Webhook signature | our app secret | **their** app secret |

The webhook is one URL for everyone. A delivery names a page, the page names a
company, and the company names the app whose secret the signature is checked
against — so two customers' apps posting to the same URL can never be confused
for one another. `GET /integrations/meta/app/` answers with exactly the three
values the owner has to paste, and the app screen shows them with a copy
button.

## Setting up the Meta app

At <https://developers.facebook.com> create a **Business** app and add the
**Facebook Login** and **Webhooks** products.

1. **Facebook Login → Settings → Valid OAuth Redirect URIs** — add exactly:

       https://<your-host>/api/b2b/integrations/meta/callback/

2. **Webhooks → Page** — subscribe to the `leadgen` field with:

       Callback URL:  https://<your-host>/api/b2b/integrations/meta/webhook/
       Verify token:  <whatever you put in META_WEBHOOK_VERIFY_TOKEN>

   Meta calls the URL once with `hub.challenge`; the view echoes it back when
   the token matches.

3. **App Review** — `leads_retrieval` and `pages_manage_metadata` are both
   reviewed permissions. Until the app is approved it works only for people
   listed on it as testers/developers, so leave `META_INTEGRATION_ENABLED`
   off in production until review passes.

## Environment

One set of values for the whole deployment. Nothing here is per customer — see
"One app, or one per company" above.

```
META_INTEGRATION_ENABLED=true

# Weel's own Facebook app. Optional if every workspace brings its own.
META_APP_ID=...
META_APP_SECRET=...
META_REDIRECT_URI=https://<your-host>/api/b2b/integrations/meta/callback/
META_WEBHOOK_VERIFY_TOKEN=<any string, must match the webhook config>

# Encrypts the stored tokens. Falls back to B2B_MAIL_SECRET_KEY if unset.
# Generate:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
B2B_INTEGRATIONS_SECRET_KEY=...
```

Then create the tables:

```
python manage.py create_b2b_tables
```

## What arrives, and how it is mapped

A lead-ad form is whatever the marketer drew, so `ingest._map_fields`
translates it. Meta's own question types come back under fixed names; custom
questions come back under a slug of the question text. Both are matched, and
Uzbek spellings of the common ones are included:

| Lead column        | Matched names                                            |
|--------------------|----------------------------------------------------------|
| `contact_full_name`| `full_name`, `name`, `ism`, `fio`, or first + last        |
| `contact_phone`    | `phone_number`, `telefon`, `raqam`, `whatsapp_number`, …  |
| `contact_email`    | `email`, `pochta`, …                                      |
| `company_name`     | `company_name`, `kompaniya`, `tashkilot`, …               |
| `contact_position` | `job_title`, `lavozim`                                    |
| `contact_address`  | `street_address`, `city`, `manzil`, `shahar`              |
| `product_name`     | `product`, `mahsulot`, `xizmat`, else the form's own name |

Everything else the customer typed is kept in `external_data.answers` **and**
written into the lead's history as its first note, so nothing is lost.

A form with neither a phone nor an email is refused: a card nobody can call is
worse than no card. It is recorded in `b2b_integration_event` as `failed`, with
the reason.

## Guarantees worth knowing

* **Nothing is ingested twice.** `b2b_integration_event` has a unique index on
  `(provider, external_id)` and the lead itself has one on
  `(company_id, source, external_id)`. Meta retries deliveries; both indexes,
  not a `SELECT`, are what decide.
* **Unsigned webhooks are dropped.** Every delivery is verified against
  `X-Hub-Signature-256` with the app secret before it is read.
* **Tokens are encrypted at rest** and no endpoint ever reads one back out.
* **Disconnecting keeps the leads.** They are real deals somebody may be
  working; unplugging the source does not take them off the board.
* **A page can be paused** without disconnecting the account —
  `PATCH /integrations/meta/pages/<id>/ {"is_active": false}`.

## When leads stop arriving

`GET /api/b2b/workspace/integrations/` reports `status`, `last_error`,
`last_sync_at` and, per page, `subscribed` and `last_error`. The two usual
causes:

* **`subscribed: false`** — the page was never subscribed to `leadgen`
  (usually a missing `pages_manage_metadata` grant). Reconnect.
* **`status: error` with an expiry warning** — a user token lasts ~60 days and
  Meta has no refresh grant for it; only the person signing in again extends
  it. The daily `b2b.integrations.refresh_meta_tokens` task marks the
  connection a week ahead so the app can ask.

`POST /integrations/meta/sync/` pulls each form's recent submissions and
raises anything the board is missing — the catch-up for deliveries that never
came. It also runs every ten minutes on its own.

---

# Claude AI / ChatGPT

A workspace plugs in its own Claude or ChatGPT account. Two things make up
the connection, and they are deliberately separate:

* **The API key.** Neither Anthropic nor OpenAI lets a third-party app sign
  a person into their *consumer* account (claude.ai / chatgpt.com) and read
  the chats there — there is no OAuth for it and no endpoint that lists
  them. What both offer is the developer API, unlocked by a key the person
  makes in their own console (`console.anthropic.com` /
  `platform.openai.com`). That key is what `POST /integrations/<provider>/`
  takes. It is checked against the vendor (`GET /models`), stored Fernet-
  encrypted in `b2b_integration.access_token_enc` like a Meta token, and
  never returned. New chats from the app are answered with it.
* **The data export.** The old chats and projects come in from the export
  both vendors let a person download from their account settings (Claude:
  *Settings → Privacy → Export data*; ChatGPT: *Settings → Data controls →
  Export data*). The ZIP is uploaded to `POST /integrations/<provider>/import/`,
  read by `ai_import.py`, and stored in `b2b_ai_project` /
  `b2b_ai_conversation` / `b2b_ai_message`. Re-importing the same export is
  idempotent on the vendor's ids. Importing works before a key is pasted.

`<provider>` is `claude` or `chatgpt`; one set of views (`ai_views.py`)
serves both, and `ai.py` is the only module that knows the two wire formats.

## Endpoints

```
GET/POST/PATCH/DELETE  /api/b2b/workspace/integrations/<provider>/
POST                   /api/b2b/workspace/integrations/<provider>/import/        multipart `file`
GET                    /api/b2b/workspace/integrations/<provider>/projects/
GET/POST               /api/b2b/workspace/integrations/<provider>/conversations/  ?project=&q=&limit=&offset=
GET/DELETE             /api/b2b/workspace/integrations/<provider>/conversations/<id>/
POST                   /api/b2b/workspace/integrations/<provider>/conversations/<id>/messages/
```

Same permissions as Meta (`CanManageIntegrations`). The list endpoint
answers three rows now — Meta, Claude, ChatGPT.

## Settings

Nothing is required server-side beyond the Fernet key
(`B2B_INTEGRATIONS_SECRET_KEY` or `B2B_MAIL_SECRET_KEY`). Optional bounds:
`B2B_AI_MAX_IMPORT_MB` (200), `B2B_AI_REQUEST_TIMEOUT` (120 s),
`B2B_AI_MAX_OUTPUT_TOKENS` (4096), `B2B_AI_HISTORY_TURNS` (40 — how many
earlier turns are sent with a new message).

Run `python manage.py create_b2b_tables` after deploying: it adds the three
tables and the `ai_model` / `ai_models` / `last_import_at` columns.
