# Performance and Capacity Runbook

## 1) Goals
- Validate API latency under expected load before go-live.
- Ensure critical queries use indexes (avoid full collection scans).
- Define release gates for performance regression.

## 2) Quick Load Test

Use built-in script:

```bash
python scripts/load_test.py --base-url http://localhost:8000 --path /api/v1/health/live --requests 500 --concurrency 50
python scripts/load_test.py --base-url http://localhost:8000 --path /api/v1/payments/providers --requests 300 --concurrency 30
```

Suggested critical endpoints:
- `GET /api/v1/health/live`
- `GET /api/v1/search/properties`
- `GET /api/v1/payments/providers`

## 3) E2E Flow Benchmark (Auth -> Booking -> Payment Create)

Run realistic API flow benchmark:

```bash
python scripts/e2e_perf_flow.py \
  --base-url http://localhost:8000 \
  --admin-email admin@homestay.local \
  --admin-password "change-me" \
  --provider vnpay \
  --iterations 20 \
  --warmup 3 \
  --output ./artifacts/e2e-baseline.json
```

The script includes:
- register perf users (host/guest)
- promote host role by admin API
- host creates property/room/availability
- guest executes `providers -> search -> booking -> payment create`

Security note:
- Use staging credentials only.
- Do not expose real production admin password in shell history/CI logs.

## 4) Index Coverage Report

Run explain-based index probe:

```bash
python scripts/index_coverage_report.py --mongo-uri "$MONGO_URI" --db-name "$MONGO_DB_NAME"
```

Review for each probe:
- `index_name` should be non-empty for critical lookups.
- `total_docs_examined` should be close to `n_returned`.
- `execution_time_ms` should remain stable between builds.

## 5) Release Gates (Recommended)

- P95 latency:
  - read endpoints: `< 300 ms`
  - write endpoints: `< 500 ms`
- Error rate: `< 1%` during load test window.
- No unexpected `COLLSCAN` on critical payment/refund queries.

## 6) Regression Workflow

1. Run baseline load test and keep output artifacts.
2. Deploy candidate build to staging.
3. Re-run same load profile.
4. Compare reports:
   - `python scripts/compare_perf_reports.py --baseline ./artifacts/e2e-baseline.json --candidate ./artifacts/e2e-candidate.json --max-regression-pct 20`
5. Compare:
   - `p95_ms`, `p99_ms`
   - error ratio by status code
   - index explain deltas
6. Block release if latency regresses >20% without justified reason.

## 7) Notes
- Current load test script is lightweight and request-level only (not distributed).
- For heavy traffic modeling, integrate k6/JMeter in CI later.
