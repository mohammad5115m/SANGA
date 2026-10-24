# Web application release integration — 2026-10-24

Branch: `codex/webapp-release-2026-10-24`.

## Sources

- `admin-panel-review-2026-10-21` at `9901a4c`: latest admin work, UI/UX,
  Docker hosting preparation, Add Product fixes (`0f7b266`, PR #7), and
  password login/reviewed registration (`78949c1`, PR #9).
- `codex/production-readiness-2026-10-23` at `8249d2a`: all three production
  hardening commits, deterministic reciprocal invoice locking, Redis export
  limits, private signature protection, container health and CI checks.

Earlier UI/UX, catalog, CRM and invoice work is retained through the application
snapshot `3697a29` on the admin branch. Its history is a snapshot rather than a
merge of the older feature branches; replaying those older branches would
reintroduce superseded code. No older branch is used to overwrite this release.

## Integration decisions

- Keep password login, admin-reviewed owner signup, and account-free customer
  contact; both OTP flows remain opt-in.
- Require PostgreSQL in production and shared Redis for export budgets.
- Preserve all seven CI jobs, including real PostgreSQL concurrency, real
  cross-process Redis and production container checks.
- Align the production import regression with PostgreSQL and an empty demo
  allowlist. This test imports settings without connecting to a database.
- Update production environment examples and the Persian hosting guide for the
  current authentication phase. The dated production audit remains historical.

## Local verification

Django system checks, migration drift, Ruff, fresh isolated SQLite migration,
and both production authentication import cases passed. Full suites and native
PDF rendering also run in Linux CI through the integration pull request.

This branch prepares the application for hosting. Real deployment credentials,
backups, DNS/TLS and operational verification are environment-specific steps in
`deployment.md` and `production-plan-fa.md`.
