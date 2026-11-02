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


# ── ADDED: Load .env file for local development ──────────────────
# Requires: pip install python-dotenv
# Production (Render) sets env vars directly — .env is optional there.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    # python-dotenv not installed — that's fine in production
    pass


# ============================================================
# SECURITY
# ============================================================

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "Set SECRET_KEY in the environment before starting Django."
    )

# ── ADDED: Warn if the default Django dev key sneaks into prod
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


# ============================================================
# APPLICATIONS
# ============================================================

INSTALLED_APPS = [
    "daphne",

    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Channels
    "channels",

    # Forms
    "crispy_forms",
    "crispy_bootstrap5",
    "widget_tweaks",

    # Project apps
    "core",
]


# ============================================================
# MIDDLEWARE
# ============================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # WhiteNoise for static files on Render
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.PrivateResponseCacheControlMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# URL / WSGI / ASGI
# ============================================================

ROOT_URLCONF = "winnitech.urls"

WSGI_APPLICATION = "winnitech.wsgi.application"
ASGI_APPLICATION = "winnitech.asgi.application"


# ============================================================
# TEMPLATES
# ============================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",

                # Custom context processors
                "core.context_processors.admin_context",
                "core.context_processors.teacher_portal_context",
            ],
        },
    },
]


# ============================================================
# CHANNELS
# ============================================================
# ── NOTE: InMemoryChannelLayer works ONLY on a single process.
# If you scale Render beyond 1 instance, switch to Redis:
#   pip install channels-redis
#   CHANNEL_LAYERS = {
#       "default": {
#           "BACKEND": "channels_redis.core.RedisChannelLayer",
#           "CONFIG": {"hosts": [os.environ["REDIS_URL"]]},
#       },
#   }

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


# ============================================================
# DATABASE
# ============================================================
#
# Render:
#   DATABASE_URL = PostgreSQL connection string
#
# Local:
#   SQLite database
#

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


# ============================================================
# PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        # ── CHANGED: 10 for staff accounts (was 8)
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# ── ADDED: Use Django's password hashing minimum iterations
# (Django 5 default is fine, but be explicit for future upgrades)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # optional; needs argon2-cffi
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]


# ============================================================
# EMAIL
# ============================================================

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST") or "localhost"
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() == "true"

# ── ADDED: Fail fast rather than hang for 30s on SMTP issues
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "15"))

# ── ADDED: Sanity-check TLS + SSL are not both enabled
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured(
        "EMAIL_USE_TLS and EMAIL_USE_SSL are mutually exclusive — pick one."
    )

DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    EMAIL_HOST_USER or "webmaster@localhost",
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

# ── ADDED: Align Django's password reset token lifetime with onboarding
PASSWORD_RESET_TIMEOUT = STAFF_ONBOARDING_TIMEOUT


# ============================================================
# INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True


# ============================================================
# STATIC FILES
# ============================================================

STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

# ── CHANGED: Django 5 uses STORAGES instead of STATICFILES_STORAGE
# (the old alias still works but emits a deprecation warning)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# ============================================================
# MEDIA FILES
# ============================================================

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ============================================================
# CLOUDINARY
# ============================================================
#
# Render's filesystem is ephemeral, so Cloudinary is recommended
# for uploaded teacher profile pictures and other media.
#
# Set this environment variable on Render:
#
# CLOUDINARY_URL=cloudinary://...
#

CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()

if CLOUDINARY_URL:
    if not CLOUDINARY_URL.startswith("cloudinary://"):
        _logger.warning(
            "CLOUDINARY_URL is set but does not start with "
            "'cloudinary://'. Cloudinary storage will not be enabled."
        )
    else:
        try:
            import cloudinary

            cloudinary.config(cloudinary_url=CLOUDINARY_URL)

            INSTALLED_APPS += [
                "cloudinary_storage",
                "cloudinary",
            ]

            # ── CHANGED: use Django 5 STORAGES dict, not DEFAULT_FILE_STORAGE
            STORAGES["default"] = {
                "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
            }

        except Exception as exc:
            _logger.exception(
                "Cloudinary failed to initialize: %s",
                exc,
            )


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# CRISPY FORMS
# ============================================================

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# ============================================================
# AUTHENTICATION
# ============================================================

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"


# ============================================================
# SCHOOL SETTINGS
# ============================================================

SCHOOL_START_TIME = "07:00"
SCHOOL_END_TIME = "15:30"


# ============================================================
# CSRF
# ============================================================

CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "https://winnetech.onrender.com",

    # Local development
    "http://localhost:8000",
    "https://localhost:8000",
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://0.0.0.0:8000",
    "https://0.0.0.0:8000",
]

# Allow additional Render/custom domains through environment variable
CSRF_TRUSTED_ORIGINS += [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]


# ============================================================
# RENDER / HTTPS
# ============================================================

# Render terminates HTTPS before forwarding requests to Django.
SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)


# Only enable these production protections when DEBUG=False.
if not DEBUG:
    # Browser should only access the site over HTTPS
    SECURE_SSL_REDIRECT = True

    # Secure cookies
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # ── ADDED: Defence-in-depth against XSS cookie theft
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"

    # Prevent browsers from MIME-sniffing responses
    SECURE_CONTENT_TYPE_NOSNIFF = True

    # Clickjacking protection
    X_FRAME_OPTIONS = "DENY"

    # ── ADDED: Referrer policy (limits token leakage in URLs)
    SECURE_REFERRER_POLICY = "same-origin"

    # ── ADDED: Django 4.1+ cross-origin opener policy
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

    # HSTS
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False  # Leave False until custom domain is stable


# ============================================================
# LOGGING
# ============================================================

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
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },

    "root": {
        "handlers": ["console"],
        # ── CHANGED: INFO in dev, WARNING in prod (less noise)
        "level": "INFO" if DEBUG else "WARNING",
    },

    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
        # ── ADDED: your app's own logger (so you can log onboarding failures etc.)
        "core": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}# ============================================================
# SILENCED CHECKS
# ============================================================
# W021 (HSTS preload) � deliberately deferred until a stable
# custom production domain exists. Enabling prematurely is a
# one-way door that can lock users out if the domain changes.
SILENCED_SYSTEM_CHECKS = ['security.W021']
