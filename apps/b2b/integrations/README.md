# Meta (Facebook / Instagram) lead ads

A company connects its own Facebook pages; the lead-ad forms on them fill the
sales funnel. Every lead that arrives this way is marked `source = meta` and
shows a "Meta’dan" badge in the app.

Only the workspace **owner** and **administrator** ("lider" in the roster's
vocabulary) can connect or disconnect it. That is enforced on the server —
`apps/b2b/integrations/permissions.py` — and reported to the app as the
`can_manage_integrations` capability so the profile row and the endpoint agree.

## The flow, end to end

1. The owner opens **Profil → Integratsiya → Meta** (or the dashboard's
   Integratsiyalar page) and taps "Ulash".
   `POST /api/b2b/workspace/integrations/meta/connect/` answers with an
   `authorize_url`; the phone opens it in its browser.
2. They sign in to Facebook, pick their pages and grant the four scopes.
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

## One app, thousands of companies

`META_APP_ID` / `META_APP_SECRET` are **Weel's own** Facebook app, and it is
the only one. A company never types an app id, a secret or a token — the owner
presses "Ulash", signs in to Facebook as themselves, ticks their pages on
Meta's own screen, and the token Meta issues is stored against their own
`company_id`:

```
settings          b2b_integration                      b2b_integration_page
──────────        ─────────────────────────────        ──────────────────────────
META_APP_ID=123   company_id=10  token=***  (Alfa)     page_id=P1  company_id=10
  (one, ours)     company_id=11  token=***  (Beta)     page_id=P2  company_id=11
                  company_id=12  token=***  (Vega)     page_id=P3  company_id=12
```

Same shape as the Gmail connection (`B2B_MAIL_GOOGLE_CLIENT_ID` is one value;
each employee's refresh token lives in `b2b_mail_account`). A workspace
bringing its own Facebook app used to be possible; it was removed on
2026-09-10 because it asked people for credentials.

### How two companies are kept apart

* **The company comes from our `state`, never from the URL.** `connect/`
  issues a random single-use state tied to the signed-in owner's company; the
  callback attaches pages only to that company.
* **A page belongs to one company.** `b2b_integration_page.page_id` is unique
  and the webhook routes every lead by it. When one person administers pages
  for several companies (an agency, a marketer, an owner of two businesses),
  Facebook hands us all of them in one login — a page another company
  already has is **left with that company**, not subscribed again, and the
  owner is told which ones and why. The rule is in `upsert_page`'s SQL, so
  two companies connecting one page at the same moment cannot both win. A
  page whose company has disconnected is free to be connected elsewhere.
* **A reconnect is the new list.** Pages the owner left unticked on Meta's
  screen are dropped from the company.
* **Every other query is scoped by `company_id`** — pages, leads, the
  "Sinxronlash" pass, notifications.

## Setting up the Meta app

At <https://developers.facebook.com> create a **Business** app and add the
**Facebook Login** and **Webhooks** products.

1. **Facebook Login → Settings → Valid OAuth Redirect URIs** — add exactly:

       https://<your-host>/api/b2b/integrations/meta/callback/

2. **Webhooks → Page** — subscribe to the `leadgen` field with:

       Callback URL:  https://<your-host>/api/b2b/integrations/meta/webhook/
       Verify token:  <whatever you put in META_WEBHOOK_VERIFY_TOKEN>

   This is done once, by Weel, for the whole deployment — companies never see
   these values.

   Meta calls the URL once with `hub.challenge`; the view echoes it back when
   the token matches.

3. **App Review** — `leads_retrieval` and `pages_manage_metadata` are both
   reviewed permissions. Until the app is approved it works only for people
   listed on it as testers/developers, so leave `META_INTEGRATION_ENABLED`
   off in production until review passes.

## Environment

One set of values for the whole deployment. Nothing here is per customer — see
"One app, thousands of companies" above.

```
META_INTEGRATION_ENABLED=true

# Weel's own Facebook app — the only one.
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
  `X-Hub-Signature-256` with Weel's app secret before it is read.
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
