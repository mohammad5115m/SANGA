from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .checks import check_media_storage

DEBUG = False

if SECRET_KEY == "unsafe-dev-only-change-me":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a strong value in production.")

# A deployment serving on loopback names is one that was started with the
# development .env still in place — a mistake worth catching at boot rather than
# through a stream of 400s. "*" is worse: it turns off the check that stops Host
# header poisoning from putting an attacker's domain into password-reset and
# share links.
_NON_PRODUCTION_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}

if "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must not be '*' in production.")
if not any(host and host not in _NON_PRODUCTION_HOSTS for host in ALLOWED_HOSTS):  # noqa: F405
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must name the real hostnames in production, "
        f"not just {sorted(_NON_PRODUCTION_HOSTS)}."
    )

# --- transport ----------------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "same-origin"

# Behind a proxy that terminates TLS, Django must be told which host header to
# trust for CSRF or every POST from the real domain is rejected.
CSRF_TRUSTED_ORIGINS = env.list(  # noqa: F405
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=[
        f"https://{host}"
        for host in ALLOWED_HOSTS  # noqa: F405
        if not host.startswith(".") and host not in _NON_PRODUCTION_HOSTS
    ],
)

CELERY_TASK_ALWAYS_EAGER = False

# --- OTP delivery must actually deliver ---------------------------------------
#
# The console provider writes the login code into the application log. In
# development that is the point; in production it means nobody receives a code
# and every code that was ever issued is sitting in a log file, which is an
# authentication bypass for anyone who can read one.
#
# So production refuses to start on a provider that does not deliver. The escape
# hatch exists because a deliberate, temporary, credential-less staging
# environment is a real thing — but it has to be asked for by name, in the
# environment, where it is visible in a deployment diff.
_ALLOW_UNDELIVERED_OTP = env.bool("SMS_ALLOW_UNDELIVERED", default=False)  # noqa: F405


def _check_sms_provider() -> None:
    from apps.accounts.sms import PROVIDERS

    provider = PROVIDERS.get((SMS_PROVIDER or "").strip().lower())  # noqa: F405
    if provider is None:
        raise ImproperlyConfigured(
            f"SMS_PROVIDER={SMS_PROVIDER!r} is not a provider SANGA knows. "  # noqa: F405
            f"Choose one of: {', '.join(sorted(PROVIDERS))}."
        )
    if not provider.delivers and not _ALLOW_UNDELIVERED_OTP:
        raise ImproperlyConfigured(
            f"SMS_PROVIDER={SMS_PROVIDER!r} does not send messages, so no user could log in "  # noqa: F405
            "and every OTP would be written to the application log. Configure a real SMS "
            "gateway, or set SMS_ALLOW_UNDELIVERED=true if this environment is deliberately "
            "unable to send."
        )


_check_sms_provider()

# --- uploaded media must survive a deploy -------------------------------------
#
# See config/settings/checks.py for why this is fail-closed rather than a warning.
check_media_storage(
    use_s3=USE_S3,  # noqa: F405
    allow_local_media=env.bool("SANGA_ALLOW_LOCAL_MEDIA", default=False),  # noqa: F405
    media_root=env("SANGA_MEDIA_ROOT", default=""),  # noqa: F405
    bucket=AWS_STORAGE_BUCKET_NAME,  # noqa: F405
    access_key=AWS_ACCESS_KEY_ID,  # noqa: F405
    secret_key=AWS_SECRET_ACCESS_KEY,  # noqa: F405
    use_iam_role=AWS_S3_USE_IAM_ROLE,  # noqa: F405
    region=AWS_S3_REGION_NAME,  # noqa: F405
    endpoint=AWS_S3_ENDPOINT_URL,  # noqa: F405
    csp_img_src=CSP_EXTRA_SOURCES.get("img-src"),  # noqa: F405
    csp_media_src=CSP_EXTRA_SOURCES.get("media-src"),  # noqa: F405
)

# --- logging ------------------------------------------------------------------
#
# The SMS logger is the one that formats the code into a message body. It is
# silenced in production regardless of the provider, so that turning the escape
# hatch on for a staging environment cannot also start writing live codes to disk
# on a host that shares a log pipeline.
LOGGING = {  # noqa: F405
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[%(asctime)s] %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},  # noqa: F405
    "loggers": {
        "apps.accounts.sms": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}
