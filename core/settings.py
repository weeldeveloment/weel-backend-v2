import logging
import sys
import os.path
import importlib.util
from urllib.parse import urlsplit

from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv, find_dotenv
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

from firebase_admin import initialize_app, credentials, get_app

import core.middleware.logging
from core.middleware.utils import CompressedTimedRotatingFileHandler

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(os.path.join(BASE_DIR, "apps"))

_default_env_path = BASE_DIR / "ops" / "monitoring" / ".env"
_found_env_path = find_dotenv()
if _found_env_path:
    load_dotenv(_found_env_path, override=True)
if _default_env_path.exists():
    load_dotenv(_default_env_path, override=True)

from users.bin_lookup import load_bin_data

DEBUG = bool(int(os.environ.get("DJANGO_DEBUG", "0")))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


USE_MINIO = env_bool("USE_MINIO", default=False)
if "test" in sys.argv:
    USE_MINIO = False
HAS_DJANGO_STORAGES = importlib.util.find_spec("storages") is not None
HAS_MINIO_STORAGE = importlib.util.find_spec("minio_storage") is not None
if USE_MINIO and not HAS_DJANGO_STORAGES:
    logging.warning(
        "USE_MINIO=true but django-storages is not installed. Falling back to local media storage."
    )
    USE_MINIO = False

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-secret-key-change-me"
        logging.warning(
            "SECRET_KEY is not set. Using an insecure development fallback key."
        )
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY is required in production. Set the SECRET_KEY environment variable."
        )

_allowed_hosts_raw = env_list("DJANGO_ALLOWED_HOSTS")
if not _allowed_hosts_raw:
    if DEBUG:
        _allowed_hosts_raw = ["localhost", "127.0.0.1"]
    else:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS must be set when DEBUG=0."
        )

ALLOWED_HOSTS = _allowed_hosts_raw.copy()
if "host.docker.internal" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("host.docker.internal")
ALLOWED_HOSTS.append("0.0.0.0")

# CORS
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
if DEBUG and not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
for _origin in (
    "https://weel.uz",
    "https://www.weel.uz",
    "https://dev.weel.uz",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
):
    if _origin not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(_origin)
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-telegram-initdata",
]

GLOBAL_APPS = [
    "django_prometheus",
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
]

USE_NORM_DATASTORE = False  # Explicitly disable norm_* datastore usage

LOCAL_APPS: list[str] = []

THIRD_PART_APPS = [
    "channels",
    "corsheaders",
    "drf_yasg",
    "rest_framework",
    "django_filters",
]
if HAS_MINIO_STORAGE:
    THIRD_PART_APPS.append("minio_storage")
if USE_MINIO and HAS_DJANGO_STORAGES:
    THIRD_PART_APPS.append("storages")

INSTALLED_APPS = GLOBAL_APPS + LOCAL_APPS + THIRD_PART_APPS

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
    "request_logging.middleware.LoggingMiddleware",  # django-request-logging
    "core.middleware.exception_logging.ExceptionLoggingMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

_db_port = os.environ.get("DB_PORT", "5432")
if _db_port in ("", "db_port"):
    _db_port = "5432"
_db_conn_max_age = int((os.environ.get("DB_CONN_MAX_AGE") or "60").strip() or "60")
_db_conn_health_checks = bool(int((os.environ.get("DB_CONN_HEALTH_CHECKS") or "1").strip() or "1"))

DATABASES = {
    "default": {
        "ENGINE": "django_prometheus.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME"),
        "USER": os.environ.get("DB_USER"),
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST"),
        "PORT": _db_port,
        "CONN_MAX_AGE": _db_conn_max_age,
        "CONN_HEALTH_CHECKS": _db_conn_health_checks,
    }
}

_redis_url = (os.environ.get("REDIS_CONNECTION_STRING") or "").strip()
_redis_socket_timeout = float((os.environ.get("REDIS_SOCKET_TIMEOUT_SECONDS") or "3").strip() or "3")
_redis_socket_connect_timeout = float(
    (os.environ.get("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS") or "3").strip() or "3"
)

# For development without Redis, fallback to local memory cache
if DEBUG and (not _redis_url or _redis_url in {
    "redis_connection_string",
    "REDIS_CONNECTION_STRING",
    "${REDIS_CONNECTION_STRING}",
}):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        },
    }
    _redis_url = None
else:
    if not _redis_url or _redis_url in {
        "redis_connection_string",
        "REDIS_CONNECTION_STRING",
        "${REDIS_CONNECTION_STRING}",
    }:
        raise ImproperlyConfigured(
            "REDIS_CONNECTION_STRING must be set to a valid Redis URL."
        )

    _redis_parts = urlsplit(_redis_url)
    if _redis_parts.scheme not in {"redis", "rediss"}:
        raise ImproperlyConfigured(
            "REDIS_CONNECTION_STRING must start with redis:// or rediss://"
        )

    CACHES = {
        "default": {
            "BACKEND": "django_prometheus.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": _redis_socket_connect_timeout,
                "SOCKET_TIMEOUT": _redis_socket_timeout,
            },
        },
    }

# Use InMemoryChannelLayer for development without Redis
if DEBUG and not _redis_url:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [_redis_url],
            },
        },
    }

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "users.authentication.PartnerJWTAuthentication",
        "users.authentication.ClientJWTAuthentication",
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "shared.throttles.SwaggerExemptAnonRateThrottle",
        "shared.throttles.SwaggerExemptUserRateThrottle",
        "shared.throttles.ResilientScopedRateThrottle",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DATETIME_FORMAT": "%Y-%m-%d %H:%M:%S",
    "EXCEPTION_HANDLER": "shared.utils.exception_errors_format_handler",
    # NOTE:
    # "1/second" for authenticated users is too strict for mobile app startup,
    # where multiple independent requests are fired in parallel (objects, stories,
    # property types, etc.). This caused frequent 429 responses.
    # Use a minute-window rate to allow short bursts while still limiting abuse.
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("API_ANON_THROTTLE_RATE", "2000/hour"),
        "user": os.environ.get("API_USER_THROTTLE_RATE", "120/minute"),
        "otp_login_send": os.environ.get("API_OTP_LOGIN_SEND_RATE", "30/minute"),
        "otp_login_verify": os.environ.get("API_OTP_LOGIN_VERIFY_RATE", "120/minute"),
        "otp_login_resend": os.environ.get("API_OTP_LOGIN_RESEND_RATE", "30/minute"),
        "otp_register_verify": os.environ.get("API_OTP_REGISTER_VERIFY_RATE", "60/minute"),
        # POST /api/user/refresh/ has no Bearer; without this it shares anon+user IP limits with all AllowAny traffic.
        "token_refresh": os.environ.get("API_TOKEN_REFRESH_RATE", "120/minute"),
        "frontend_log": "2000/hour",
    },
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "type",
    "JTI_CLAIM": "jti",
}

_swagger_url = (os.getenv("SWAGGER_URL") or "").strip() or None
# In local debug, let Swagger use the current origin (localhost) to avoid
# cross-origin "NetworkError" when SWAGGER_URL points to a remote domain.
SWAGGER_URL = None if DEBUG else _swagger_url
ENABLE_SWAGGER_UI = env_bool("ENABLE_SWAGGER_UI", default=DEBUG)
SWAGGER_BASIC_AUTH_USERNAME = (os.getenv("SWAGGER_BASIC_AUTH_USERNAME") or "").strip()
SWAGGER_BASIC_AUTH_PASSWORD = (os.getenv("SWAGGER_BASIC_AUTH_PASSWORD") or "").strip()
SWAGGER_BASIC_AUTH_MAX_ATTEMPTS = int(
    (os.getenv("SWAGGER_BASIC_AUTH_MAX_ATTEMPTS") or "3").strip() or "3"
)
SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS = int(
    (os.getenv("SWAGGER_BASIC_AUTH_LOCKOUT_SECONDS") or "900").strip() or "900"
)
PROMETHEUS_ENABLED = env_bool("PROMETHEUS_ENABLED", default=DEBUG)
SWAGGER_SETTINGS = {
    # Do not require Django session login for docs; everything is viewable anonymously.
    "USE_SESSION_AUTH": False,
    "LOGIN_URL": None,
    "LOGOUT_URL": None,
    "DEFAULT_MODEL_RENDERING": "example",
    "DISPLAY_OPERATION_ID": True,
    "DOC_EXPANSION": "none",
    "OPERATIONS_SORTER": "alpha",
    "TAGS_SORTER": "alpha",
    "DEEP_LINKING": True,
    "SHOW_EXTENSIONS": True,
    "SHOW_COMMON_EXTENSIONS": True,
    "PERSIST_AUTH": True,
    "SECURITY_DEFINITIONS": {
        "Bearer": {
            "type": "apiKey",
            "name": "Authorization",
            "in": "header",
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

load_bin_data()

# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en"

TIME_ZONE = "Asia/Tashkent"

USE_I18N = True

LANGUAGES = [
    ("en", _("English")),
    ("ru", _("Russian")),
    ("uz", _("Uzbek")),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

USE_L10N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/



STATIC_URL = "/static/"

MEDIA_URL = "/media/"

STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles/")

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]


# Default: local media storage. Overridden below when USE_MINIO=True.
_media_root = os.getenv("MEDIA_ROOT", os.path.join(BASE_DIR, "media"))
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": _media_root},
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

WHITENOISE_USE_FINDERS = True

WHITENOISE_USE_FINDERS = True
WHITENOISE_MANIFEST_STRICT = False

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

MEDIA_ROOT = _media_root

if USE_MINIO and HAS_DJANGO_STORAGES:
    # Endpoint URL: MINIO_ENDPOINT_URL, or build from MINIO_STORAGE_ENDPOINT, or MINIO_BROWSER_REDIRECT_URL
    _endpoint = (
        os.getenv("MINIO_ENDPOINT_URL")
        or os.getenv("MINIO_BROWSER_REDIRECT_URL")
    )
    if not _endpoint and os.getenv("MINIO_STORAGE_ENDPOINT"):
        _host = os.getenv("MINIO_STORAGE_ENDPOINT").strip()
        _use_https = (os.getenv("MINIO_STORAGE_USE_HTTPS") or "false").lower() == "true"
        _proto = "https" if _use_https else "http"
        _endpoint = _proto + "://" + _host if not (_host.startswith("http://") or _host.startswith("https://")) else _host
    MINIO_ENDPOINT_URL = _endpoint

    MINIO_ACCESS_KEY = (
        os.getenv("MINIO_ACCESS_KEY")
        or os.getenv("MINIO_STORAGE_ACCESS_KEY")
        or os.getenv("MINIO_ROOT_USER")
    )
    MINIO_SECRET_KEY = (
        os.getenv("MINIO_SECRET_KEY")
        or os.getenv("MINIO_STORAGE_SECRET_KEY")
        or os.getenv("MINIO_ROOT_PASSWORD")
    )

    if not MINIO_ENDPOINT_URL:
        raise ImproperlyConfigured(
            "MinIO is enabled but endpoint is not set. Set MINIO_ENDPOINT_URL, MINIO_STORAGE_ENDPOINT, or MINIO_BROWSER_REDIRECT_URL."
        )
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        raise ImproperlyConfigured(
            "MinIO is enabled but credentials are missing. Set MINIO_ACCESS_KEY/MINIO_SECRET_KEY, "
            "MINIO_STORAGE_ACCESS_KEY/MINIO_STORAGE_SECRET_KEY, or MINIO_ROOT_USER/MINIO_ROOT_PASSWORD."
        )

    AWS_S3_ENDPOINT_URL = MINIO_ENDPOINT_URL
    AWS_ACCESS_KEY_ID = MINIO_ACCESS_KEY
    AWS_SECRET_ACCESS_KEY = MINIO_SECRET_KEY
    AWS_STORAGE_BUCKET_NAME = (
        os.getenv("MINIO_BUCKET_NAME")
        or os.getenv("MINIO_STORAGE_MEDIA_BUCKET_NAME")
        or "weel-media"
    )
    AWS_S3_REGION_NAME = os.getenv("MINIO_REGION", "us-east-1")
    AWS_S3_ADDRESSING_STYLE = os.getenv("MINIO_ADDRESSING_STYLE", "path")
    AWS_QUERYSTRING_AUTH = env_bool("MINIO_QUERYSTRING_AUTH", default=False)
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("MINIO_QUERYSTRING_EXPIRE", "3600"))
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_VERIFY = env_bool("MINIO_VERIFY_SSL", default=True)
    MINIO_PUBLIC_MEDIA_URL = (os.getenv("MINIO_PUBLIC_MEDIA_URL") or "").strip()
    if MINIO_PUBLIC_MEDIA_URL and not AWS_QUERYSTRING_AUTH:
        if "://" not in MINIO_PUBLIC_MEDIA_URL:
            MINIO_PUBLIC_MEDIA_URL = "https://" + MINIO_PUBLIC_MEDIA_URL
        parsed_public_media = urlsplit(MINIO_PUBLIC_MEDIA_URL)
        if parsed_public_media.netloc:
            # Support base URLs including bucket path, e.g. https://host/weel-media
            AWS_S3_CUSTOM_DOMAIN = (
                parsed_public_media.netloc + parsed_public_media.path
            ).rstrip("/")
            if parsed_public_media.scheme:
                AWS_S3_URL_PROTOCOL = parsed_public_media.scheme + ":"
        else:
            logging.warning(
                "MINIO_PUBLIC_MEDIA_URL is invalid: %s", MINIO_PUBLIC_MEDIA_URL
            )
    elif MINIO_PUBLIC_MEDIA_URL and AWS_QUERYSTRING_AUTH:
        logging.info(
            "MINIO_PUBLIC_MEDIA_URL is ignored because MINIO_QUERYSTRING_AUTH=1 "
            "(signed URLs are generated from MINIO_ENDPOINT_URL)."
        )

    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }
else:
    os.makedirs(MEDIA_ROOT, exist_ok=True)

# MinIO Storage (for django-minio-storage backend / scripts; main app uses S3 when USE_MINIO=True)
MINIO_STORAGE_ENDPOINT = os.getenv("MINIO_STORAGE_ENDPOINT") or os.getenv("MINIO_ENDPOINT_URL", "")
MINIO_STORAGE_ACCESS_KEY = os.getenv("MINIO_STORAGE_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY", "")
MINIO_STORAGE_SECRET_KEY = os.getenv("MINIO_STORAGE_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY", "")
MINIO_STORAGE_USE_HTTPS = (os.getenv("MINIO_STORAGE_USE_HTTPS") or os.getenv("MINIO_USE_HTTPS", "false")).lower() == "true"
MINIO_STORAGE_MEDIA_BUCKET_NAME = os.getenv("MINIO_STORAGE_MEDIA_BUCKET_NAME") or os.getenv("MINIO_BUCKET_NAME", "weel-media")
MINIO_STORAGE_AUTO_CREATE_MEDIA_BUCKET = True
MINIO_STORAGE_MEDIA_USE_PRESIGNED = True
_minio_proto = "https" if MINIO_STORAGE_USE_HTTPS else "http"
MINIO_STORAGE_MEDIA_URL = f"{_minio_proto}://{MINIO_STORAGE_ENDPOINT}/{MINIO_STORAGE_MEDIA_BUCKET_NAME}" if MINIO_STORAGE_ENDPOINT else ""

# We only use MinIO for Media files, not Static files
# DEFAULT_FILE_STORAGE is deprecated in Django 4.2+


# Django limits high enough to accept uploads
FILE_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024  # 100MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 150 * 1024 * 1024  # 150MB

PHOTO_SIZE_TO_COMPRESS = 5 * 1024 * 1024  # 5MB
VIDEO_SIZE_TO_COMPRESS = 10 * 1024 * 1024  # 10MB

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100MB


ALLOWED_PHOTO_EXTENSION = ["jpg", "jpeg", "png", "heif", "heic"]
ALLOWED_VIDEO_EXTENSION = ["mp4", "mov", "avi", "mkv"]

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django Unfold

# Eskiz
ESKIZ_LOGIN_URL = os.getenv("ESKIZ_LOGIN_URL")
ESKIZ_SMS_SEND_URL = os.getenv("ESKIZ_SMS_SEND_URL")
ESKIZ_EMAIL = os.getenv("ESKIZ_EMAIL")
ESKIZ_PASSWORD = os.getenv("ESKIZ_PASSWORD")
ESKIZ_SENDER = os.getenv("ESKIZ_SENDER", "")
ESKIZ_CALLBACK_URL = os.getenv("ESKIZ_CALLBACK_URL", "")

# DEBUG=True da Celery tasklari sinxron ishlaydi — worker ishlamasa ham OTP SMS yuboriladi
CELERY_TASK_ALWAYS_EAGER = DEBUG

# Jwt Token Issuer
JWT_ISSUER = (os.getenv("JWT_ISSUER") or "weel-backend").strip() or "weel-backend"

# Test user - OTP so'ralmaydi (development va production)
TEST_USER_PHONE_NUMBER = (os.getenv("TEST_USER_PHONE_NUMBER") or "").strip() or None
TEST_PARTNER_PHONE_NUMBER = (
    (os.getenv("TEST_PARTNER_PHONE_NUMBER") or "").strip() or None
)

# Plum
PLUM_AUTH_TOKEN = os.getenv("PLUM_AUTH_TOKEN")

# Current currency exchange rate endpoint
CURRENT_CURRENCY_EXCHANGE_RATE = os.getenv(
    "CURRENT_CURRENCY_EXCHANGE_RATE",
    "https://open.er-api.com/v6/latest/USD",
)

# Service fee (percentage)
SERVICE_FEE = (os.getenv("SERVICE_FEE") or "20").strip() or "20"

# Telegram Bot
TELEGRAM_BOT_TOKEN_APP = os.getenv("TELEGRAM_BOT_TOKEN_APP")
BOT_TOKEN = TELEGRAM_BOT_TOKEN_APP
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://partners.weel.uz/")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://api.weel.uz")
FRONTEND_LOG_TOKEN = (os.getenv("FRONTEND_LOG_TOKEN") or "").strip()

# Firebase
FIREBASE_APP = None
firebase_credentials_path = Path(
    os.getenv("FIREBASE_CREDENTIALS_PATH", BASE_DIR / "certificates/certificate.json")
)

if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(firebase_credentials_path)

try:
    FIREBASE_APP = get_app()
except ValueError:
    if firebase_credentials_path.exists():
        try:
            FIREBASE_APP = initialize_app(
                credentials.Certificate(str(firebase_credentials_path))
            )
        except Exception as firebase_error:
            logging.exception(
                "Failed to initialize Firebase app from %s: %s",
                firebase_credentials_path,
                firebase_error,
            )
    else:
        logging.warning(
            "Firebase credentials file not found at %s. Firebase features are disabled.",
            firebase_credentials_path,
        )

# Security settings
if DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    CSRF_COOKIE_SECURE = True
    CSRF_TRUSTED_ORIGINS = [
        "https://api.weel.uz",
        "https://api.node.v1.backend.weel.uz",
        "https://weel.uz",
        "https://www.weel.uz",
        "https://dev.weel.uz",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7 * 52  # one year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    SECURE_HSTS_PRELOAD = True

# Logging
LOGS_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

REQUEST_LOGGING_DATA_LOG_LEVEL = logging.INFO
REQUEST_LOGGING_ENABLE_COLORIZE = False  # disable colors for JSON
REQUEST_LOGGING_HTTP_4XX_LOG_LEVEL = logging.WARNING
REQUEST_LOGGING_HTTP_5XX_LOG_LEVEL = logging.ERROR
REQUEST_LOGGING_MAX_BODY_LENGTH = 50000  # log request/response body up to 50 MB
REQUEST_LOGGING_SENSITIVE_HEADERS = [
    "Authorization",
    "Cookie",
    "X-Csrftoken",
    "X-Telegram-InitData",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "()": "core.middleware.logging.UnicodeConsoleFormatter",
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": "core.middleware.logging.UnicodeJsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
        "file": {
            "level": "INFO",
            "()": CompressedTimedRotatingFileHandler,
            "filename": os.path.join(LOGS_DIR, "app.log"),
            "when": "midnight",
            "interval": 1,  # every 1 day
            "backupCount": 14,  # keep 14 days of logs
            "formatter": "json",
            "encoding": "utf-8",
        },
        "file_frontend": {
            "level": "INFO",
            "()": CompressedTimedRotatingFileHandler,
            "filename": os.path.join(LOGS_DIR, "frontend.log"),
            "when": "midnight",
            "interval": 1,
            "backupCount": 14,
            "formatter": "json",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "core": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "users": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "frontend": {
            "handlers": ["console", "file_frontend"],
            "level": "INFO",
            "propagate": False,
        },
        "..sanatorium": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
