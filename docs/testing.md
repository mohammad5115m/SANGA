# Testing Guide

## Run tests

```bash
pytest
python manage.py check
```

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
