# Backend Operations Runbook

## 1) Deployment (Production Profile)

### Prerequisites
- Docker + Docker Compose
- MongoDB tools (`mongodump`, `mongorestore`) installed on ops machine
- Production `.env` with strong secrets and production-safe values

### Deploy steps
1. Prepare environment:
   - `ENVIRONMENT=production`
   - `MONGO_URI`, `MONGO_DB_NAME`
   - payment/refund secrets
   - webhook allowlists:
     - `PAYMENT_WEBHOOK_ALLOWED_IPS`
     - `REFUND_WEBHOOK_ALLOWED_IPS`
2. Build and run:
   - `docker compose -f docker-compose.prod.yml up -d --build`
3. Verify health:
   - Liveness: `/api/v1/health/live`
   - Readiness: `/api/v1/health/ready`
   - Base health: `/api/v1/health`
   - Metrics snapshot: `/api/v1/health/metrics`

## 2) Backup

### Linux/macOS
- `MONGO_URI` and `MONGO_DB_NAME` must be exported.
- Run:
  - `chmod +x ./scripts/backup_mongo.sh`
  - `./scripts/backup_mongo.sh ./backups`

### Windows (PowerShell)
- Set `$env:MONGO_URI` and `$env:MONGO_DB_NAME`.
- Run:
  - `./scripts/backup_mongo.ps1 -BackupDir .\backups`

## 3) Restore

### Linux/macOS
- `MONGO_URI` and `MONGO_DB_NAME` must be exported.
- Run:
  - `chmod +x ./scripts/restore_mongo.sh`
  - `./scripts/restore_mongo.sh ./backups/mongo-<db>-<timestamp>`

### Windows (PowerShell)
- Set `$env:MONGO_URI` and `$env:MONGO_DB_NAME`.
- Run:
  - `./scripts/restore_mongo.ps1 -BackupFolder .\backups\mongo-<db>-<timestamp>`

## 4) Restore Drill Checklist
- Restore into a staging DB/cluster, not production first.
- Verify:
  - user login
  - booking create/cancel
  - payment callback processing
  - refund reconcile path
- Check `/api/v1/health/metrics` counters after smoke tests.

## 5) Incident Triage
- Check API health/readiness first.
- Inspect dead letters in `dead_letters` collection:
  - `category=payment_webhook`
  - `category=refund_webhook`
  - `category=job`
- Correlate with request/audit logs and retry only after root cause is understood.

## 6) Security Notes
- Never keep `PAYMENT_WEBHOOK_ALLOWED_IPS=["*"]` in production.
- Rotate all secrets before go-live.
- Keep backup files encrypted at rest and access-restricted.

## 7) Performance Validation
- Run lightweight load checks before each release:
  - `python scripts/load_test.py --base-url http://localhost:8000 --path /api/v1/health/live --requests 500 --concurrency 50`
  - `python scripts/load_test.py --base-url http://localhost:8000 --path /api/v1/payments/providers --requests 300 --concurrency 30`
- Run E2E flow benchmark for realistic regression signals:
  - `python scripts/e2e_perf_flow.py --base-url http://localhost:8000 --admin-email <admin-email> --admin-password <admin-password> --provider vnpay --iterations 20 --warmup 3 --output ./artifacts/e2e-report.json`
- Compare baseline/candidate reports:
  - `python scripts/compare_perf_reports.py --baseline ./artifacts/e2e-baseline.json --candidate ./artifacts/e2e-candidate.json --max-regression-pct 20`
- Run index coverage probe:
  - `python scripts/index_coverage_report.py --mongo-uri "$MONGO_URI" --db-name "$MONGO_DB_NAME"`
- Follow detailed thresholds and rollout gates in `PERFORMANCE_RUNBOOK.md`.
