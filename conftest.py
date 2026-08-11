from __future__ import annotations

import os

# Keep local pytest independent from Docker Postgres defaults in .env
os.environ.setdefault("DJANGO_DATABASE", "sqlite")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
