# AGENTS.md

SANGA (سنگا) is a Django 5.1 monolith: a Persian, right-to-left B2B/B2C stone
inventory platform. Standard developer commands live in `README.md` and
`docs/testing.md`; read those first. This file only records non-obvious,
durable context for automated agents.

## Cursor Cloud specific instructions

The environment update script provisions a Python 3.12 virtualenv at `.venv/`
and installs `requirements/development.txt`. It also creates `.env` from
`.env.example` on first run if missing. Always invoke Python through
`.venv/bin/python` (or activate the venv); there is no global project install.

Key points not obvious from the README:

- **Database:** local dev and tests use SQLite. `.env` ships with
  `DJANGO_DATABASE=sqlite`, and `conftest.py` forces SQLite for pytest
  regardless of `.env`. Postgres/Redis (via `docker-compose.yml`) are only
  needed if you specifically want to exercise the Postgres/Celery-broker path;
  they are NOT required to run the app or the test suite.
- **Celery:** in development settings `CELERY_TASK_ALWAYS_EAGER=True`, so tasks
  run inline. You do not need to start the `worker`/`beat` services or Redis for
  normal development.
- **Run the app:** `.venv/bin/python manage.py runserver 0.0.0.0:8000`. App at
  `http://localhost:8000/`, health check at `/health/`.
- **Migrations are NOT part of the update script.** After pulling changes, run
  `.venv/bin/python manage.py migrate` yourself when models changed.
- **Login is OTP-only (no passwords).** In DEBUG the OTP code is shown on the
  verify page as an info flash (`SMS_PROVIDER=console` also logs it). Seed demo
  accounts with `.venv/bin/python manage.py seed_demo`; the supplier login phone
  is `09121111111` and the partner is `09122222222`.
- **Checks:** `.venv/bin/pytest` (251 tests, SQLite), plus
  `.venv/bin/python manage.py check`. `ruff` is installed but the repo ships no
  ruff config, so `ruff check .` reports many findings against ruff defaults;
  it is not part of the documented gate (README lists only pytest + check).
- **No frontend build step.** CSS is hand-written in `static/css/app.css`;
  Node.js is not used.
