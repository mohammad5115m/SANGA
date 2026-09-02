from __future__ import annotations

from .base import *  # noqa: F403

DEBUG = True

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Manifest storage is awkward without collectstatic during early local loops.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}

# Fictional local demo accounts. Requesting a login OTP provisions/reactivates
# only these explicitly approved phones; every other unknown phone stays closed.
SANGA_LOGIN_PHONE_ALLOWLIST = env.list(  # noqa: F405
    "SANGA_LOGIN_PHONE_ALLOWLIST",
    default=["09121111111", "09122222222"],
)

# Real CustomerLead/Inquiry rows remain the source of truth. This adds isolated,
# fictional CRM examples and keeps demo mutations in the current browser session.
SANGA_CRM_DEMO_ENABLED = env.bool("SANGA_CRM_DEMO_ENABLED", default=True)  # noqa: F405
