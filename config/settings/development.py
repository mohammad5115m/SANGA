from __future__ import annotations

from .base import *  # noqa: F403

DEBUG = True

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Manifest storage is awkward without collectstatic during early local loops.
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
