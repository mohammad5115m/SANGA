# Deployment Guide (Initial)

## Local Docker

1. Copy `.env.example` to `.env` and set a strong `DJANGO_SECRET_KEY`.  
2. Run `docker compose up --build`.  
3. Open http://localhost:8000/  
4. OTP codes appear in the `web` container logs when `SMS_PROVIDER=console`.

## Production checklist (Phase 9 detail later)

- `DJANGO_SETTINGS_MODULE=config.settings.production`  
- `DEBUG=false`  
- TLS via reverse proxy  
- Managed PostgreSQL + Redis  
- S3-compatible media (`USE_S3=true`)  
- Celery worker + beat running  
- Regular DB + media backups  
- Never cache B2B/public price pages aggressively  

## Backups (minimum)

- Nightly PostgreSQL dump  
- Media bucket versioning/replication  
- Store restore runbook beside deploy scripts  
