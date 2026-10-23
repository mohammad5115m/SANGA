# AGENTS.md

Sang Esf is a Django 5.1 monolith: a Persian, right-to-left B2B/B2C stone
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
- **Login defaults to phone + password.** Public owner registrations wait for
  platform-admin approval before creating a User/Business. Retail inquiries
  require a phone but no account or OTP, and do not mark new phones verified.
  Old accounts need a password assigned via admin or `manage.py changepassword`.
  Seed demo accounts with `.venv/bin/python manage.py seed_demo`; supplier phone
  is `09121111111`, partner is `09122222222`. No shared default password is set.
  Future OTP is opt-in: `AUTH_LOGIN_METHOD=otp`, `CUSTOMER_OTP_ENABLED=true`.
  Legacy OTP tests explicitly enable those switches; test the default password
  and customer-contact flow separately.
- **Checks:** `.venv/bin/pytest` (251 tests, SQLite), plus
  `.venv/bin/python manage.py check`. `ruff` is installed but the repo ships no
  ruff config, so `ruff check .` reports many findings against ruff defaults;
  it is not part of the documented gate (README lists only pytest + check).
- **No frontend build step.** CSS is hand-written in `static/css/app.css`;
  Node.js is not used.
