# core/test_settings.py
import os
import warnings

# core.settings refuses to import with DEBUG=0 unless the provider base URLs
# are named explicitly, so that a production deploy cannot fall back to test
# inventory. A test run is DEBUG=0 too but is not production, and no test
# talks to a real provider (the clients are mocked), so answer the guard here
# rather than making every developer and every CI step export these.
os.environ.setdefault("BOOKHARA_BASE_URL", "https://avia-api-dev.bookhara.uz")
os.environ.setdefault("HOTELIOS_BASE_URL", "https://integration-staging.hotelios.uz")

from .settings import *

DEBUG = False

# Silence known third-party warning noise in test output.
warnings.filterwarnings(
    "ignore",
    message=r".*doesn't match a supported version!.*",
    category=Warning,
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}


class DisableMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

# Ensure Celery in tests never tries Redis/network. Eager mode is what
# guarantees that — tasks run inline and the broker is never contacted.
#
# The URL has to be present and syntactically valid even so: core/celery.py
# refuses to import without one. Blanking it only worked on machines with a
# .env to fall back on; on a bare checkout (a CI runner, a fresh clone) every
# test module that reaches a Celery task died during collection.
os.environ["REDIS_CONNECTION_STRING"] = "redis://localhost:6379/0"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable password hashing for faster tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Disable debug toolbar if used
DEBUG_TOOLBAR_CONFIG = {
    'SHOW_TOOLBAR_CALLBACK': lambda request: False
}

# Use faster password hasher for tests
AUTH_PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Use in-memory cache for tests (avoid external Redis dependency).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

# Force in-memory channel layer in tests (avoid channels_redis dependency).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Keep booking price calculations stable in tests.
SERVICE_FEE = "20"
