# Testing Guide

## Run tests

```bash
pytest
python manage.py check
```

## Critical coverage areas

1. OTP request/verify and rate limits  
2. Tenant isolation (memberships/warehouses/lots)  
3. B2B price non-leakage via `resolve_visible_prices`  
4. Reservation quantity locking (Phase 6)  
5. Visibility matrix (Phase 2+)  

## Conventions

- Prefer service-level unit tests for business rules  
- Add authorization negative tests for every security-sensitive endpoint  
- Use factories in later phases (`factory-boy`)  
