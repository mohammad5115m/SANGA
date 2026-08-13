# Two stages: wheels are built with a compiler, and the runtime does not get one.
#
# The previous image installed requirements/development.txt, kept build-essential
# around, ran as root and started Django's development server. Every one of those
# is fine locally and none of them belongs in front of real users: runserver is
# single-threaded, does not serve static files safely, and prints tracebacks.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ /tmp/requirements/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /tmp/requirements/production.txt \
       -c /tmp/requirements/constraints.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

# libpq for psycopg and curl for the container health check. No compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 sanga

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=sanga:sanga . /app

# Static files are collected at build time, so a replica starting under load is
# not also compressing CSS, and the image is identical to the one that was tested.
# The build must not need a real secret or database to do it.
RUN DJANGO_SETTINGS_MODULE=config.settings.build python manage.py collectstatic --noinput

USER sanga

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["web"]
