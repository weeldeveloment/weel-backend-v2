# Raw schema baseline

Most tables in this project are not Django models. They are raw SQL, and for
most of them the definition has never existed anywhere except inside the
running database. The consequences showed up repeatedly:

- A fresh database cannot be built from the repository, so the endpoint smoke
  suite (every route, asserting no 500s) cannot run in CI.
- The two tenant-provisioning paths drifted apart unnoticed for months —
  registering created `pms_room_type` but no Booking.com tables, while the
  management command did the reverse — because each carried its own copy of
  the DDL.
- A new environment can only be created by copying an old one.

`public_baseline.sql` closes that gap. It is **not committed yet**: it has to
be generated from a database that is known good, and only you have one.

## Generating it

Run against **staging**, or a restored production dump — never against the
database serving traffic if you can avoid it. The command only reads.

```bash
python manage.py dump_raw_schema --output schema/public_baseline.sql
```

Then commit the file. Review the diff first: it is the authoritative record of
the schema from that point on.

Do not hand-edit it. To change the schema, change the database and regenerate,
or add the change to the code that owns those tables
(`platform.raw_repository.create_tenant_schema` for `pms_*`,
`create_b2b_tables` for `b2b_*`).

## Using it

```bash
python manage.py bootstrap_schema
```

Applies, in order: the `postgis` and `vector` extensions, Django migrations,
this baseline, and the `pms_*` / `b2b_*` DDL the code creates at runtime. It is
idempotent, and it warns rather than fails when the baseline is missing.

Verified end to end: dumping a populated database and bootstrapping an empty
one reproduces all 467 columns identically.

## What is owned where

| Tables | Owner |
|---|---|
| `auth_*`, `django_*`, `recommendation_*`, `*_embeddings` | Django migrations |
| `pms_*` | `platform.raw_repository.create_tenant_schema` |
| `b2b_*` | `create_b2b_tables` |
| `platform_*` | `create_platform_schema` |
| everything else (`users`, `booking`, `property`, `chat_*`, `notification`, ...) | this baseline |

`spatial_ref_sys` belongs to PostGIS and is deliberately excluded.

## Once the baseline is committed

Two things become possible, and both should be done:

1. Drop the `users` scaffold from the root `conftest.py`. It is a hand-written
   approximation that exists only so the integration tests can run at all; the
   baseline replaces it with the real definition.
2. Enable the endpoint smoke suite in CI — set `WEEL_SMOKE_DB=1` on the
   `integration-test` job. Expect real failures at first: roughly 87 endpoint
   checks currently fail against a partially built schema, and nobody has
   established which of those are genuine 500s.
