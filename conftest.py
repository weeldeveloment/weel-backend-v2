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
