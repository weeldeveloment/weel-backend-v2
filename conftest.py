"""Pytest bootstrap.

Apps are imported by their short name (``shared.raw.db``, ``users.tokens``)
because ``core/settings.py`` appends ``apps/`` to ``sys.path``. During test
collection pytest imports test modules — and through them the app modules —
before Django settings are loaded, so that path has to be in place here or
collection dies with ``ModuleNotFoundError: No module named 'shared'``.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APPS_DIR = BASE_DIR / "apps"

for path in (str(BASE_DIR), str(APPS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Pin the settings module before anything imports Django. An inherited
# DJANGO_SETTINGS_MODULE from the shell would otherwise win and could point
# tests at the real database.
os.environ["DJANGO_SETTINGS_MODULE"] = "core.test_settings"

# pytest-django resolves its settings during load_initial_conftests, which
# runs before this file is imported, so it has already decided not to call
# django.setup(). Test modules import app code at collection time and that
# code touches the app registry, so set Django up here.
import django  # noqa: E402

django.setup()


# ─── Integration schema bootstrap ────────────────────────────────────────────
#
# The integration suites read raw-SQL tables that no Django migration owns:
# the b2b tables and `users`. pytest-django builds its own database and
# applies migrations to it, which leaves both missing — that is why those
# suites errored out and ended up skipped.
#
# This only runs when WEEL_INTEGRATION_DB=1, so the default sqlite suite is
# untouched.

import pytest  # noqa: E402

# `users` has no DDL anywhere in the repository — it exists only in the live
# database. This is the shape the code touches (see
# users/raw_repository._insert_user), reconstructed so the integration suites
# can run. It is a test scaffold, not a schema of record.
_USERS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.users (
    id BIGSERIAL PRIMARY KEY,
    guid UUID NOT NULL DEFAULT gen_random_uuid(),
    role VARCHAR(32) NOT NULL,
    email VARCHAR(254),
    phone_number VARCHAR(32) UNIQUE,
    username VARCHAR(150),
    password VARCHAR(128),
    first_name VARCHAR(150),
    last_name VARCHAR(150),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    if os.getenv("WEEL_INTEGRATION_DB") != "1":
        yield
        return

    from django.core.management import call_command
    from django.db import connection

    with django_db_blocker.unblock():
        # Migrations already ran when pytest-django built this database.
        call_command("create_b2b_tables", verbosity=0)

        with connection.cursor() as cursor:
            cursor.execute(_USERS_TABLE_DDL)

    yield
