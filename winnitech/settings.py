"""
Django settings for Winneba Technical Institute Staff Management System.
Configured for local development and deployment on Render.
"""

import logging
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

import dj_database_url

_logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "Set SECRET_KEY in the environment before starting Django."
    )

if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured(
        "Refusing to start with 'django-insecure-' SECRET_KEY in production."
    )

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "ALLOWED_HOSTS",
        ".onrender.com,localhost,127.0.0.1",
    ).split(",")
    if host.strip()
]


INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "crispy_forms",
    "crispy_bootstrap5",
    "widget_tweaks",
    "core",
    "gmail_oauth_backend",
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.PrivateResponseCacheControlMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "winnitech.urls"
WSGI_APPLICATION = "winnitech.wsgi.application"
ASGI_APPLICATION = "winnitech.asgi.application"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.admin_context",
                "core.context_processors.teacher_portal_context",
            ],
        },
    },
]


CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]


# ============================================================
# EMAIL — Gmail API via django-gmail-oauth-backend
# ============================================================

GMAIL_OAUTH_CLIENT_ID = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
GMAIL_OAUTH_CLIENT_SECRET = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")

EMAIL_BACKEND = "gmail_oauth_backend.GmailOauthBackend"

DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "kaytech2007@gmail.com",
)

try:
    STAFF_ONBOARDING_TIMEOUT = int(
        os.environ.get("STAFF_ONBOARDING_TIMEOUT", str(48 * 60 * 60))
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "STAFF_ONBOARDING_TIMEOUT must be a positive number of seconds."
    ) from exc
if STAFF_ONBOARDING_TIMEOUT <= 0:
    raise ImproperlyConfigured(
        "STAFF_ONBOARDING_TIMEOUT must be a positive number of seconds."
    )

PASSWORD_RESET_TIMEOUT = STAFF_ONBOARDING_TIMEOUT


LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()

if CLOUDINARY_URL and CLOUDINARY_URL.startswith("cloudinary://"):
    try:
        import cloudinary
        cloudinary.config(cloudinary_url=CLOUDINARY_URL)
        INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]
        STORAGES["default"] = {
            "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
        }
    except Exception as exc:
        _logger.exception("Cloudinary failed to initialize: %s", exc)


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"


SCHOOL_START_TIME = "07:00"
SCHOOL_END_TIME = "15:30"


# ============================================================
# CSRF — trusted origins for Render + localhost
# ============================================================
# NOTE: Django does NOT support wildcards in CSRF_TRUSTED_ORIGINS.
# The Render URL is read from RENDER_EXTERNAL_URL env var (auto-set by Render)
# or from the CSRF_TRUSTED_ORIGINS env var (comma-separated list).

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "https://localhost:8000",
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://0.0.0.0:8000",
    "https://0.0.0.0:8000",
]

# Render sets RENDER_EXTERNAL_URL automatically
_render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
if _render_url:
    CSRF_TRUSTED_ORIGINS.append(_render_url)

# Any extra origins passed via env var
CSRF_TRUSTED_ORIGINS += [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Deduplicate while preserving order
_seen = set()
CSRF_TRUSTED_ORIGINS = [
    o for o in CSRF_TRUSTED_ORIGINS
    if not (o in _seen or _seen.add(o))
]


SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{levelname} {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO" if DEBUG else "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


SILENCED_SYSTEM_CHECKS = ["security.W021"]