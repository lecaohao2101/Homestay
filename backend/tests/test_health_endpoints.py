def test_liveness_endpoint(test_context):
    client = test_context["client"]
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"


def test_readiness_endpoint(monkeypatch, test_context):
    client = test_context["client"]
    monkeypatch.setattr("app.api.v1.health.ping_mongodb", lambda: True)
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ready"
    assert payload["database"] == "connected"
