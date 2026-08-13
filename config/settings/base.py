from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    USE_S3=(bool, False),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-dev-only-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_htmx",
    "django_celery_beat",
    "apps.core",
    "apps.accounts",
    "apps.businesses",
    "apps.pricing",
    "apps.inventory",
    "apps.catalog",
    "apps.inquiries",
    "apps.marketplace",
    "apps.notifications",
    "apps.purchase_requests",
    "apps.trading",
    "apps.invoicing",
    "apps.accounting",
    "apps.reporting",
    # Retired apps that must stay installed.
    #
    # apps.contacts still owns a table: LedgerEntry.contact is a PROTECT FK
    # holding pre-V2 rows whose counterparty could not be mapped to a Business.
    # Those rows are read-only history — see docs/accounting.md. Its UI is gone.
    "apps.contacts",
    # The rest hold no models at all, only migration history that other apps
    # depend on. Removing them breaks `migrate` on an empty database:
    # partners.0002 hands SavedSearch to apps.marketplace, accounting.0001
    # references reservations.Reservation, and pricing.0002 references
    # contacts.Contact.
    "apps.partners",
    "apps.matching",
    "apps.reservations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.businesses.middleware.CurrentBusinessMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.businesses.context_processors.business_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

_db_engine = env("DJANGO_DATABASE", default="postgres")
if _db_engine == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # Overridable so the fresh-migrate check can target a throwaway file
            # instead of the developer's working database.
            "NAME": env("SANGA_SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", default="sanga"),
            "USER": env("POSTGRES_USER", default="sanga"),
            "PASSWORD": env("POSTGRES_PASSWORD", default="sanga"),
            "HOST": env("POSTGRES_HOST", default="localhost"),
            "PORT": env("POSTGRES_PORT", default="5432"),
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fa"
LANGUAGES = [
    ("fa", "فارسی"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Django 5.1 removed STATICFILES_STORAGE / DEFAULT_FILE_STORAGE; STORAGES is
# the only supported way to configure storage backends.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
# FORMS_URLFIELD_ASSUME_HTTPS was a transitional setting for the Django 5.0
# change to URLField's default scheme. That behaviour is now the default, and
# the setting is deprecated in 5.2 and removed in 6.0.

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "businesses:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
# Deliberately empty. Stock and price freshness are derived from timestamps at
# read time, so nothing needs to sweep rows on a schedule. The Celery wiring is
# kept because notifications and future async work will want it; an idle broker
# costs nothing, whereas re-introducing the plumbing later is disruptive.
CELERY_BEAT_SCHEDULE: dict[str, dict] = {}

SMS_PROVIDER = env("SMS_PROVIDER", default="console")
#: Seconds to wait on the SMS gateway. Short on purpose: a login page that hangs
#: for thirty seconds is a login page people give up on, and the OTP is worthless
#: by the time a slow gateway delivers it anyway.
SMS_TIMEOUT_SECONDS = env.float("SMS_TIMEOUT_SECONDS", default=10.0)
#: Kavenegar credentials. Never committed; production refuses to start without
#: them when SMS_PROVIDER=kavenegar.
KAVENEGAR_API_KEY = env("KAVENEGAR_API_KEY", default="")
#: The name of a template already approved in the Kavenegar panel. The lookup
#: endpoint substitutes one token into it — the code — so the wording lives with
#: the operator, not in this repository.
KAVENEGAR_OTP_TEMPLATE = env("KAVENEGAR_OTP_TEMPLATE", default="")
OTP_EXPIRY_SECONDS = env.int("OTP_EXPIRY_SECONDS", default=300)
OTP_LENGTH = env.int("OTP_LENGTH", default=6)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_REQUEST_COOLDOWN_SECONDS = env.int("OTP_REQUEST_COOLDOWN_SECONDS", default=60)
OTP_MAX_REQUESTS_PER_HOUR = env.int("OTP_MAX_REQUESTS_PER_HOUR", default=10)
# Every other limit keys on the phone number, so a caller with a list of numbers
# could request a code for each and never touch one — SANGA paying the gateway
# for each, and each recipient getting an unexplained message. An address is not
# a strong identity, but a limit that costs an attacker a proxy per hundred
# messages is worth far more than no limit at all.
OTP_MAX_REQUESTS_PER_IP_PER_HOUR = env.int("OTP_MAX_REQUESTS_PER_IP_PER_HOUR", default=30)

#: How many reverse proxies sit in front of SANGA and append to
#: ``X-Forwarded-For``.
#:
#: Zero means the header is ignored entirely and ``REMOTE_ADDR`` is used, which
#: is correct for a directly-reached deployment and the safe answer for a
#: misconfigured one. A header is written by whoever sent the request, so
#: trusting its leftmost value — the old behaviour — let any caller pick their
#: own rate-limit key, or somebody else's. Set this to the real number of hops
#: and make the edge proxy overwrite rather than append. See docs/deployment.md.
SANGA_TRUSTED_PROXY_COUNT = env.int("SANGA_TRUSTED_PROXY_COUNT", default=0)

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@sanga.local")

#: Only platform-verified Businesses appear in the colleague directory, the
#: colleague marketplace, public search and shared catalogs.
#:
#: On by default. SANGA has no self-service signup, so provisioning an account is
#: itself the approval, and ``create_business_for_owner`` records it. The setting
#: exists so a development or demo database seeded with unverified fixtures is not
#: an empty site; it is not a way to run production with an open network.
SANGA_REQUIRE_VERIFIED_FOR_NETWORK = env.bool("SANGA_REQUIRE_VERIFIED_FOR_NETWORK", default=True)

# Content-Security-Policy knobs; see apps.core.middleware.
# Report-only first is how a policy survives contact with a live site.
CSP_REPORT_ONLY = env.bool("CSP_REPORT_ONLY", default=False)
#: Extra origins per directive, for object storage serving product media.
CSP_EXTRA_SOURCES: dict[str, list[str]] = {
    "img-src": env.list("CSP_IMG_SRC", default=[]),
    "media-src": env.list("CSP_MEDIA_SRC", default=[]),
    "connect-src": env.list("CSP_CONNECT_SRC", default=[]),
}

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "apps.accounts.sms": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "django.request": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}

USE_S3 = env.bool("USE_S3", default=False)
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default=None)
#: Let the host supply credentials (instance profile, workload identity) instead
#: of putting long-lived keys in the environment.
AWS_S3_USE_IAM_ROLE = env.bool("AWS_S3_USE_IAM_ROLE", default=False)

if USE_S3:
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    # Stored objects are served straight to browsers, so the type they are served
    # with is a security setting, not a convenience: an uploaded file offered as
    # text/html executes in the origin it is served from. nosniff stops a browser
    # second-guessing the declared type.
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400", "ContentDisposition": "inline"}
    STORAGES["default"] = {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"}

#: Where local media lives when object storage is deliberately not used. Named
#: explicitly rather than defaulted, so the setting and the mounted volume cannot
#: drift apart without somebody noticing. See config/settings/checks.py.
if env("SANGA_MEDIA_ROOT", default=""):
    MEDIA_ROOT = env("SANGA_MEDIA_ROOT")
