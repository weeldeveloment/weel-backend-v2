# Raw schema baseline

Most tables in this project are not Django models. They are raw SQL, and for
most of them the definition has never existed anywhere except inside the
running database. The consequence: a fresh database cannot be built from the
repository, so the endpoint smoke suite (every route, asserting no 500s)
cannot run in CI, and a new environment can only be created by copying an
old one.

`public_baseline.sql` would close that gap. It is **not committed yet**: it
has to be generated from a database that is known good, and only you have
one.

## Generating it

Run against **staging**, or a restored production dump — never against the
database serving traffic if you can avoid it. The command only reads.

```bash
python manage.py dump_raw_schema --output schema/public_baseline.sql
```

Then commit the file. Review the diff first: it is the authoritative record of
the schema from that point on.

Do not hand-edit it. To change the schema, change the database and regenerate,
or add the change to the code that owns those tables (`create_b2b_tables` for
`b2b_*`).

## Using it

```bash
python manage.py bootstrap_schema
```

Applies, in order: the `postgis` and `vector` extensions, Django migrations,
this baseline, and the `b2b_*` DDL the code creates at runtime. It is
idempotent, and it warns rather than fails when the baseline is missing.

## What is owned where

| Tables | Owner |
|---|---|
| `auth_*`, `django_*`, `recommendation_*`, `*_embeddings` | Django migrations |
| `b2b_*` | `create_b2b_tables` |
| everything else (`users`, `booking`, `property`, `chat_*`, `notification`, ...) | this baseline |

`spatial_ref_sys` belongs to PostGIS and is deliberately excluded.

## Once the baseline is committed

Two things become possible, and both should be done:

1. Drop the `users` scaffold from the root `conftest.py`. It is a hand-written
   approximation that exists only so the integration tests can run at all; the
   baseline replaces it with the real definition.
2. Enable the endpoint smoke suite in CI — set `WEEL_SMOKE_DB=1` on the
   `integration-test` job.
