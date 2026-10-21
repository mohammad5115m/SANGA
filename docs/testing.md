# Testing Guide

## Run tests

```bash
pytest
python manage.py check
python manage.py makemigrations --check
ruff check .
```

## The PostgreSQL lane is not a slower copy of the fast one

```bash
./scripts/run_pg_tests.sh                  # the whole suite, on PostgreSQL
./scripts/run_pg_tests.sh -m concurrency   # only the tests SQLite cannot run
```

Tests marked `@pytest.mark.concurrency` **skip themselves on SQLite**, because
SQLite serializes writers behind one database lock and ignores
`select_for_update` entirely. A concurrency test that passes there proves
nothing: the second caller always arrives after the first has finished, so the
test is green whether the locking is right or absent.

These are the only tests that exercise the row locks and partial unique indexes
the financial and OTP invariants depend on, so CI fails the job if any of them
were **collected but skipped** — a silently-skipped concurrency suite is worse
than none, because the green tick claims something nobody checked.

They earn it. One of them caught the `cancel` versus `accept` ordering rule
during the final hardening pass, on PostgreSQL, after passing on SQLite.

## Definition of done for every phase

A change is not finished until all five of these are green:

1. `python manage.py check`
2. `python manage.py makemigrations --check` — no unrecorded model changes
3. `pytest`
4. `./scripts/check_fresh_migrate.sh` — `migrate` against a **brand-new empty database**
5. `ruff check .`

Step 4 is the one that is easy to forget and the one that catches a prematurely
deleted migration-only app. See [v2-migration-strategy.md](./v2-migration-strategy.md).

## Critical coverage areas

1. **Provisioning** — OTP never creates a User; unknown and inactive phones give
   the same message; a challenge is burned even when the login is refused
2. **Tenant isolation** — no business reaches another's products, ledger or invoices
3. **B2B non-leakage** — checked across *every* public surface at once
   (`apps/core/tests/test_security_invariants.py`)
4. **Lifecycle** — hidden / unavailable / deleted products leave search,
   marketplace, storefront, share links and catalogs simultaneously
5. **Freshness** — expiry degrades the display without hiding the product;
   confirmation restores it
6. **Plan gates** — browse-only businesses blocked at the *service* layer, seat
   limits enforced on add
7. **Accept is not sell** — acceptance creates no Trade and no ledger entry
8. **Exactly-once** — double finalization produces one Trade and one entry;
   issuing the invoice never posts again
9. **Snapshots** — invoices and trades survive product rename and deletion
10. **Catalogs** — rule membership changes as products change; a withdrawn product
    leaves every mode
11. **Public inquiries** — multiple products, saved before any share action, no
    platform User created
12. **Reports** — totals validated against fixtures whose arithmetic is done in
    the test
13. **Query budgets** — flat in row count, so an N+1 fails immediately
    (`apps/core/tests/test_query_budgets.py`)
14. **Idempotency** — one submission token is one sale, one ledger pair and one
    invoice, sequentially and under contention
15. **State transitions** — a request with a Trade can never settle as cancelled
    or rejected, under any interleaving
16. **Media limits** — a decompression bomb is refused before it is decoded
17. **Normalization** — a product stored with Arabic letterforms is found by a
    query typed with Persian ones, and the reverse

## Branch protection

CI is only a gate if it blocks. `master` requires:

- a pull request before merge, with no direct pushes;
- the branch up to date with `master` before merging;
- all of these checks passing:

| Check | Job |
|-------|-----|
| Lint and system checks | `lint` |
| Tests (SQLite) | `tests` |
| Tests and concurrency (PostgreSQL) | `postgres` |
| Fresh migrate from zero | `migrations` |
| Production settings fail closed | `deploy_check` |
| Dependency vulnerabilities | `dependency_audit` |

The dependency audit became blocking once the pins were clean. It was advisory
while they carried known vulnerabilities, which meant the job was permanently red
and therefore told nobody anything.

To apply, in **Settings → Branches → Add branch ruleset** for `master`: require a
pull request, require status checks to pass, select the six jobs above, and
require branches to be up to date. This is documented rather than applied because
it needs repository-admin rights that the tooling here does not have.

## Conventions

- Prefer service-level unit tests for business rules  
- Add authorization negative tests for every security-sensitive endpoint  
- Use the shared builders in `apps/core/testing.py`. Their defaults produce a
  sellable, publicly visible product; a test that cares about a lifecycle state
  sets it explicitly, which keeps the interesting part of the test visible
- `factory-boy` is not used and not required
