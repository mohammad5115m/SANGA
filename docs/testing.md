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
4. Trade-recording idempotency (one live entry per business per accepted offer)  
5. Visibility matrix (Phase 2+)  
6. Network privacy: what an open marketplace must **not** expose across businesses
   (`apps/marketplace/tests/test_network_privacy.py`)  

## Conventions

- Prefer service-level unit tests for business rules  
- Add authorization negative tests for every security-sensitive endpoint  
- Use factories in later phases (`factory-boy`)  
