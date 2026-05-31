from fastapi.testclient import TestClient
from app.main import app


def test_metrics_endpoint_exposes_platform_metrics():
    client = TestClient(app)
    body = client.get("/metrics").text
    # The metric families must be registered (they appear once instrumented
    # code is imported, even before any observation).
    for name in (
        "awp_node_execution_seconds",
        "awp_node_runs_total",
        "awp_workflow_runs_total",
        "awp_llm_tokens_total",
        "awp_nodes_in_flight",
    ):
        assert name in body


def test_track_node_records_success_and_error():
    from app.observability import metrics
    from prometheus_client import REGISTRY

    with metrics.track_node("TransformAgent"):
        pass
    val = REGISTRY.get_sample_value(
        "awp_node_runs_total",
        {"node_type": "TransformAgent", "status": "success"},
    )
    assert val and val >= 1

    try:
        with metrics.track_node("TransformAgent"):
            raise ValueError("boom")
    except ValueError:
        pass
    err = REGISTRY.get_sample_value(
        "awp_node_runs_total",
        {"node_type": "TransformAgent", "status": "error"},
    )
    assert err and err >= 1