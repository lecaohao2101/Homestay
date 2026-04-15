from app.core.observability import snapshot_metrics


def test_request_id_header_is_generated(test_context):
    client = test_context["client"]
    resp = client.get("/api/v1/payments/providers")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


def test_request_id_header_can_be_provided(test_context):
    client = test_context["client"]
    resp = client.get("/api/v1/payments/providers", headers={"X-Request-ID": "req-test-001"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == "req-test-001"


def test_health_metrics_endpoint_returns_http_and_business_metrics(test_context):
    client = test_context["client"]
    state = test_context["state"]
    state["current_user"] = test_context["admin_user"]

    client.get("/api/v1/payments/providers")
    client.post(
        "/api/v1/admin/dashboard/backfill-money-minor/jobs",
        json={"dry_run": True, "batch_size": 100, "run_now": False},
    )

    metrics_resp = client.get("/api/v1/health/metrics")
    assert metrics_resp.status_code == 200
    payload = metrics_resp.json()
    assert "http_requests" in payload
    assert "business_events" in payload
    assert isinstance(payload["http_requests"], list)
    assert isinstance(payload["business_events"], list)

    snapshot = snapshot_metrics()
    http_keys = [item["key"] for item in snapshot["http_requests"]]
    assert any(key.startswith("GET /api/v1/payments/providers ") for key in http_keys)
    business_events = [item["event"] for item in snapshot["business_events"]]
    assert "backfill.job.create" in business_events
