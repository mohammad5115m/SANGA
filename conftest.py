from __future__ import annotations

import os

import pytest

# Keep local pytest independent from Docker Postgres defaults in .env.
# ``setdefault`` rather than an assignment, so the PostgreSQL lane
# (``scripts/run_pg_tests.sh``, and the CI job that mirrors it) can point the
# same suite at the production engine by exporting DJANGO_DATABASE=postgres.
os.environ.setdefault("DJANGO_DATABASE", "sqlite")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")


@pytest.fixture(autouse=True)
def _concurrency_needs_a_real_database(request):
    """Skip ``@pytest.mark.concurrency`` tests unless PostgreSQL is in use.

    SQLite serializes writers with a single database-level lock and ignores
    ``select_for_update`` entirely, so a concurrency test that passes there
    proves nothing about production. These tests are not "slow variants" of the
    fast suite — they are the only place where the row locks and partial unique
    indexes this codebase relies on are actually exercised.
    """
    if request.node.get_closest_marker("concurrency") is None:
        return
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("needs PostgreSQL: SQLite cannot demonstrate row locking or partial unique indexes")
