# core/test_settings.py
import os
import warnings

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

# Ensure Celery in tests never tries Redis/network.
os.environ["REDIS_CONNECTION_STRING"] = ""
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
