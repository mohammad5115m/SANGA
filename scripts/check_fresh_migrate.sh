#!/usr/bin/env bash
# Verify that `migrate` succeeds against a brand-new empty database.
#
# This is the check that catches a migration-only app removed too early, or a
# data migration that only works against an already-populated schema. It is
# separate from `pytest` because pytest builds its test database from the same
# migration graph but tolerates a lot more.
set -euo pipefail

DB_PATH="${1:-$(mktemp -u /tmp/sanga-fresh-XXXXXX.sqlite3)}"
rm -f "$DB_PATH"

DJANGO_DATABASE=sqlite \
DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.development}" \
SANGA_SQLITE_PATH="$DB_PATH" \
    python3 manage.py migrate --no-input

echo "fresh migrate OK -> $DB_PATH"
rm -f "$DB_PATH"
