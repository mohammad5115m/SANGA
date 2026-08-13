# Deployment

## 1. Local development

```bash
cp .env.example .env
docker compose up --build
```

- App: http://localhost:8000/
- Health: http://localhost:8000/health/
- OTP codes appear in the `web` container log, because `SMS_PROVIDER=console`.

`docker-compose.yml` is a development file: it bind-mounts the source, publishes
the database and Redis ports, and runs `runserver`. Do not deploy it.

## 2. Production topology

`docker-compose.prod.yml` and the multi-stage `Dockerfile` are the production
path. What differs from development is the whole point:

| | Development | Production |
|---|---|---|
| Server | `runserver` | Gunicorn |
| Image | dev dependencies, build tools, root | production deps only, no compiler, uid 10001 |
| Source | bind-mounted | baked into the image |
| Static | served by Django | `collectstatic` at build time, WhiteNoise at runtime |
| Migrations | on every `web` start | a one-shot `migrate` service, before the replicas |
| Database, Redis | ports published | internal network only |

TLS terminates at a reverse proxy in front of the stack.
`SECURE_PROXY_SSL_HEADER`, `SECURE_SSL_REDIRECT` and `CSRF_TRUSTED_ORIGINS`
assume that.

### One image, four roles

```bash
docker run … sanga migrate    # run migrations and exit
docker run … sanga web        # gunicorn
docker run … sanga worker     # celery worker
docker run … sanga beat       # celery beat
docker run … sanga check      # manage.py check --deploy
```

Migrations are deliberately **not** run by `web`. Every replica starting at once
would race the same migration, and a schema change would run while the old code
is still serving. A release is: run `migrate` once as its own step, confirm it,
then roll the web replicas.

## 3. Production refuses to start when it is misconfigured

`config/settings/production.py` raises `ImproperlyConfigured` at import — before
anything serves a request — for each of these:

| Condition | Why it is fatal |
|-----------|-----------------|
| `DJANGO_SECRET_KEY` is the development default | Session and signature forgery |
| `DJANGO_ALLOWED_HOSTS` names only loopback | The development `.env` is still in place |
| `DJANGO_ALLOWED_HOSTS` is `*` | Host header poisoning into share and reset links |
| `SMS_PROVIDER` is not a known provider | A typo used to fall back to the console provider |
| `SMS_PROVIDER` cannot deliver | Nobody can log in, and every OTP is in the log |
| `USE_S3=false` without `SANGA_ALLOW_LOCAL_MEDIA` | Uploads are written into the container and discarded on the next deploy |
| `SANGA_ALLOW_LOCAL_MEDIA` without `SANGA_MEDIA_ROOT` | The volume mount and the setting can drift apart unnoticed |
| `USE_S3=true` without a bucket, credentials or region/endpoint | Every upload fails at the moment a user makes one |
| `USE_S3=true` without `CSP_IMG_SRC` / `CSP_MEDIA_SRC` | The policy blocks every product image, and it looks like missing data |

The last one is the one worth dwelling on. The console provider writes the login
code into the application log. In development that is the point. In production it
means no user receives a code and every code that was ever issued is sitting in a
log file — an authentication bypass for anyone who can read one. Failing to boot
is the correct response, and a loud one.

`SMS_ALLOW_UNDELIVERED=true` is the escape hatch for a deliberately
credential-less staging environment. It has to be asked for by name, in the
environment, where it shows up in a deployment diff. The `apps.accounts.sms`
logger is silenced under production settings regardless, so turning the hatch on
cannot start writing live codes to a shared log pipeline.

Verify before deploying:

```bash
python manage.py check --deploy --fail-level WARNING
```

## 3a. Uploaded media

**Object storage is the supported production strategy.** Set `USE_S3=true` with a
bucket, credentials (or `AWS_S3_USE_IAM_ROLE=true`), a region or endpoint, and the
storage origin in `CSP_IMG_SRC` and `CSP_MEDIA_SRC`.

The reason this is enforced rather than recommended: with `USE_S3=false` the
default storage writes into the container's filesystem, Django only routes
`MEDIA_URL` while `DEBUG` is on, and nothing mounted a volume at that path. Every
product photo was written somewhere unreachable and thrown away on the next
deploy, with nothing in any log to say so. That is the worst class of
misconfiguration — one whose only symptom arrives weeks later, from a customer.

The alternative is supported but has to be asked for by name:

```bash
SANGA_ALLOW_LOCAL_MEDIA=true
SANGA_MEDIA_ROOT=/app/media
```

Choosing it means taking on two things Django will not do for you:

1. **Mount a persistent volume** at `SANGA_MEDIA_ROOT`. `docker-compose.prod.yml`
   declares `media_data:/app/media` for exactly this.
2. **Serve `/media/` from the reverse proxy**, reading that same volume. Django
   does not serve media with `DEBUG=False`, so without this every image 404s.

Serve media with `X-Content-Type-Options: nosniff`. An uploaded file offered to a
browser as `text/html` executes in the origin it is served from; SANGA validates
what it stores, but the header is the part that survives a validation bug.

## 4. Required environment

| Variable | Notes |
|----------|-------|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` |
| `DJANGO_SECRET_KEY` | 50+ random characters. Never reused across environments. |
| `DJANGO_ALLOWED_HOSTS` | Real hostnames, comma-separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Defaults to `https://` + each host |
| `DJANGO_DATABASE` | `postgres` |
| `POSTGRES_*` | Managed database credentials |
| `REDIS_URL`, `CELERY_BROKER_URL` | Managed Redis |
| `SMS_PROVIDER` | A gateway that delivers |
| `USE_S3`, `AWS_*` | Object storage for product media. Required unless the local-media mode is declared. |
| `CSP_IMG_SRC`, `CSP_MEDIA_SRC` | The storage/CDN origin, so images are not blocked. Required with `USE_S3`. |
| `SANGA_ALLOW_LOCAL_MEDIA`, `SANGA_MEDIA_ROOT` | Only for the volume-backed alternative in §3a |
| `SECURE_HSTS_SECONDS` | One year by default |

Secrets come from the environment, never from a file in the image.

## 5. Dependencies are pinned

`requirements/*.txt` say what SANGA depends on. `requirements/constraints.txt`
says which versions were reviewed and tested, and the image builds with `-c`. Two
builds of one commit install the same bytes; a rebuild after an upstream release
does not ship something nobody looked at.

Upgrading is a reviewed change of its own:

```bash
pip install -r requirements/production.txt --upgrade
pip freeze > requirements/constraints.txt   # then prune dev-only entries
```

CI runs `pip-audit` against the constraints file on every change, and it is
**blocking**. It was advisory while the pins carried known vulnerabilities, which
meant the job was permanently red and therefore told nobody anything — an
advisory check that never passes is indistinguishable from one that never runs.

When a new advisory lands, the fix is to upgrade. Where there is genuinely no
upstream fix, add an explicit `--ignore-vuln` to the CI step together with a note
in `FINAL_HARDENING_IMPLEMENTATION_REPORT.md` recording who accepted the risk and
why. Both are visible in review; `continue-on-error` was not.

Watch the support window as well as the CVE list. Django 5.1 was pinned here
until it had been end-of-life for months, which is a standing vulnerability that
no audit reports because no advisory is ever filed against an unsupported branch.
SANGA tracks the LTS line: 5.2 is supported to April 2028.

## 6. Frontend assets

Alpine and HTMX are **self-hosted and version-pinned** in `static/vendor/`. A CDN
script tag is a standing grant of arbitrary code execution on every SANGA page to
whoever controls that origin, including any future owner of the package name.

Serving them ourselves is also what makes the Content-Security-Policy worth
having: `script-src 'self'` with nothing carved out. The last inline script moved
into `static/js/app.js` for the same reason — a policy containing `unsafe-inline`
is close to no policy at all.

Google Fonts remains the one third-party origin, named explicitly in `style-src`
and `font-src`. A stylesheet cannot execute; a script can.

Set `CSP_REPORT_ONLY=true` first on a live site, watch for violations, then
enforce. A policy introduced straight to enforcing is a policy that gets reverted
in a hurry, and a reverted policy protects nobody.

## 7. Backups

- Nightly `pg_dump`, retained off-host.
- Media bucket versioning or replication.
- **The restore is the thing that is tested, not the dump.** A backup nobody has
  restored is a hypothesis. Restore into a scratch database quarterly and run
  `python manage.py check` plus a smoke test against it.

```bash
# Backup
pg_dump --format=custom "$DATABASE_URL" > sanga-$(date +%F).dump

# Restore, into a scratch database
createdb sanga_restore_test
pg_restore --dbname=sanga_restore_test --no-owner sanga-2026-08-13.dump
DJANGO_DATABASE=postgres POSTGRES_DB=sanga_restore_test python manage.py check
```

## 8. Health and monitoring

`/health/` returns 200 when the process can serve. The container `HEALTHCHECK`
and any load balancer probe should use it.

Logs go to stdout in a single line format for a log collector to pick up. The SMS
logger is silenced in production; nothing else logs a credential.

## 9. Upgrading from V1

See [v2-migration-strategy.md](./v2-migration-strategy.md), particularly §5. The
visibility collapse is not information-preserving, and the migration deliberately
does **not** publish items that were previously colleague-only. Take a backup
before migrating, and keep it: it is the only record of the old `visibility`
column once `inventory.0006` has run.
