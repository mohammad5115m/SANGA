#!/usr/bin/env bash
# Container entrypoint. One image, three roles, selected by the command.
#
#   web      gunicorn, the thing that serves requests
#   worker   celery worker
#   beat     celery beat
#   migrate  run migrations and exit
#
# Migrations are deliberately *not* run by `web`. Every replica starting at once
# would race the same migration, and a schema change would run while old code is
# still serving. Releasing means: run `migrate` once, as its own step, then roll
# the web replicas.
set -euo pipefail

: "${DJANGO_SETTINGS_MODULE:=config.settings.production}"
export DJANGO_SETTINGS_MODULE

: "${GUNICORN_WORKERS:=3}"
: "${GUNICORN_TIMEOUT:=60}"
: "${PORT:=8000}"

role="${1:-web}"
shift || true

case "$role" in
  web)
    exec gunicorn config.wsgi:application \
      --bind "0.0.0.0:${PORT}" \
      --workers "${GUNICORN_WORKERS}" \
      --timeout "${GUNICORN_TIMEOUT}" \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      --forwarded-allow-ips '*' \
      "$@"
    ;;
  worker)
    exec celery -A config worker --loglevel "${CELERY_LOG_LEVEL:-info}" "$@"
    ;;
  beat)
    exec celery -A config beat --loglevel "${CELERY_LOG_LEVEL:-info}" "$@"
    ;;
  migrate)
    exec python manage.py migrate --no-input "$@"
    ;;
  check)
    exec python manage.py check --deploy "$@"
    ;;
  *)
    # Anything else is run verbatim, so `docker run image python manage.py shell`
    # still works without a second image.
    exec "$role" "$@"
    ;;
esac
