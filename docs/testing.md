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

1. OTP request/verify and rate limits  
2. Tenant isolation (memberships/warehouses/lots)  
3. B2B price non-leakage via `resolve_visible_prices` / `resolve_prices_for_viewer`  
4. ContactPrice: only the linked colleague sees the override; public/catalog never does  
5. Trade-recording idempotency (one live entry per business per accepted offer)  
6. Ledger balance math, reversals, aging FIFO, archived contacts with non-zero balances  
7. Contacts CRUD, unique linked-business constraint, archive/restore + ContactPrice suspension  
8. Visibility matrix (private / colleagues / public)  
9. Network privacy: what an open marketplace must **not** expose across businesses
   (`apps/marketplace/tests/test_network_privacy.py`)  
10. Dashboard query bound + `ledger.view` gate on financial sections
    (`apps/businesses/tests/test_dashboard.py`)  

## Conventions

- Prefer service-level unit tests for business rules  
- Add authorization negative tests for every security-sensitive endpoint  
- Prefer plain model factories / helpers in tests; `factory-boy` is optional and not required  
