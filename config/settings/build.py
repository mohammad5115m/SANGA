"""Settings for build-time tasks only — `collectstatic`, and nothing else.

Collecting static files needs the app to import, which under the production
settings means a real secret, real hostnames and a working SMS gateway. Those
belong in the deployment environment, not in the image build, and baking them
into a layer would put them in the image.

This is not a runtime configuration and must never be one: `DEBUG` is off but
none of the production hardening is here, because nothing serves a request under
it.
"""

from __future__ import annotations

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "build-time-only-never-serves-a-request"  # noqa: S105
ALLOWED_HOSTS: list[str] = []
