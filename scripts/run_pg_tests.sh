#!/usr/bin/env bash
# Run the whole suite against PostgreSQL, including the concurrency lane.
#
# The default `pytest` run uses SQLite because it is fast and needs no service.
# SQLite cannot demonstrate `select_for_update`, partial unique indexes under
# contention, or two writers racing, so every financial and OTP invariant that
# depends on those is only really tested here. This is a required check, not an
# optional one.
set -euo pipefail

export DJANGO_DATABASE=postgres
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.development}"
export POSTGRES_DB="${POSTGRES_DB:-sanga}"
export POSTGRES_USER="${POSTGRES_USER:-sanga}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-sanga}"
export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"

echo "running the suite against postgresql://${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
exec python -m pytest "$@"
