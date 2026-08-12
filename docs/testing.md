# Testing Guide

## Run tests

```bash
pytest
python manage.py check
python manage.py makemigrations --check
ruff check .
```

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

## Conventions

- Prefer service-level unit tests for business rules  
- Add authorization negative tests for every security-sensitive endpoint  
- Use the shared builders in `apps/core/testing.py`. Their defaults produce a
  sellable, publicly visible product; a test that cares about a lifecycle state
  sets it explicitly, which keeps the interesting part of the test visible
- `factory-boy` is not used and not required
