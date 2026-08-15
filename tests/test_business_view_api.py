"""The Business View HTTP surface.

What matters at this layer, beyond the projection's own correctness:

*   the default screen's payload contains no raw model output — a person has to
    open the technical route by name to see any (§5, §46, §60);
*   an action is accepted only if the projection this person was served
    actually offered it, so the rendered screen and the accepted commands
    cannot disagree (§54);
*   the narration is fetched once per meaningful state change and served from
    cache thereafter (§17, §50).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.business_view_fixtures import PUMP_SPEC, basf_run

RUN_ID = "run-basf-001"


@pytest.fixture(scope="module", autouse=True)
def _unthrottled():
    """This module makes more requests per minute than the shared budget
    allows; the limiter has its own coverage in test_production_controls.py."""
    from app.config import settings

    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = original


class FakeCostLedger:
    def run_summary(self, run_id: str, session_id: str | None = None) -> dict:
        return {
            "run_id": run_id,
            "by_node": [{
                "node_id": "understand_message", "model": "claude-sonnet-4-5",
                "intended_model": "auto", "cost_usd": 0.0018, "latency_ms": 1400,
                "task_type": "extraction", "provider": "anthropic",
                "fallback_used": False, "fallback_reason": None,
            }],
        }


class FakeNarrations:
    def __init__(self):
        self.docs: dict[tuple, dict] = {}
        self.reads = 0

    async def find_one(self, query, projection=None):
        self.reads += 1
        return self.docs.get((query["run_id"], query["session_id"], query["state_version"]))

    async def update_one(self, query, update, upsert=False):
        key = (query["run_id"], query["session_id"], query["state_version"])
        self.docs[key] = {**query, **update["$set"]}
        return type("R", (), {"matched_count": 1})()

    async def create_index(self, *args, **kwargs):
        return None


class FakeRunHistory:
    def __init__(self):
        self.updates: list[tuple[dict, dict]] = []

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update))
        return type("R", (), {"matched_count": 1})()


class FakeDB:
    def __init__(self):
        self.narrations = FakeNarrations()
        self.run_history = FakeRunHistory()

    def __getitem__(self, name: str) -> Any:
        if name == "business_narrations":
            return self.narrations
        if name == "run_history":
            return self.run_history
        raise KeyError(name)


class ScriptedLLM:
    """Returns a valid, fully-grounded narration."""

    def __init__(self):
        self.calls = 0

    async def complete_structured(self, *, response_model, **kwargs):
        self.calls += 1
        return response_model.model_validate(
            {"headline": "Ready for Inside Sales", "summary": "BASF SE needs a quotation.", "next_step": ""}
            if "headline" in response_model.model_fields
            else {"summary": "…", "fact_refs": [], "rule_refs": []}
        )


@pytest.fixture(scope="module")
def db():
    return FakeDB()


@pytest.fixture(scope="module")
def llm():
    return ScriptedLLM()


@pytest.fixture(scope="module")
def client(_unthrottled, db, llm, request):
    run = basf_run()
    run["workflow_yaml"] = PUMP_SPEC.model_dump_json()  # replaced below

    with TestClient(app) as instance:
        instance.post("/auth/token", data={"username": "ayush", "password": "dev123"})

        services = app.state.services
        original = {key: services.get(key) for key in ("audit_db", "cost_ledger", "llm", "mcp")}
        services["audit_db"] = db
        services["cost_ledger"] = FakeCostLedger()
        services["llm"] = llm
        services["mcp"] = None

        # Serve our fixture run for this run id, whatever the store holds.
        import app.api.runs as runs_module

        async def fake_get_run(_db, _scope, run_id):
            if run_id != RUN_ID:
                return None
            fixture = basf_run()
            fixture["workflow_yaml"] = open(
                "workflows/pump_manufacturer_case_routing.yaml", encoding="utf-8"
            ).read()
            return fixture

        original_get_run = runs_module.get_run
        runs_module.get_run = fake_get_run
        try:
            yield instance
        finally:
            runs_module.get_run = original_get_run
            for key, value in original.items():
                if value is None:
                    services.pop(key, None)
                else:
                    services[key] = value


class TestProjectionEndpoint:
    def test_it_serves_a_business_shaped_work_item(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-projection").json()

        assert body["work_item"]["title"] == "BASF SE — Quotation request"
        assert body["business_status"]["headline"] == "Ready for Inside Sales"
        assert [a["id"] for a in body["activities"]] == [
            "understand", "enrich", "handling", "ownership", "outcome",
        ]
        assert body["decision"]["headline"] == "Inside Sales"

    def test_the_payload_carries_no_raw_model_output(self, client):
        raw = client.get(f"/api/runs/mine/{RUN_ID}/business-projection").text

        # The extraction node's `raw` is a JSON *string* of the model's whole
        # answer; embedded in this response it would appear escaped. Field ids
        # like `understanding:has_safety_issue` are fine and necessary — the
        # blob is what must not be here.
        assert '"raw"' not in raw
        assert '\\"english_summary\\"' not in raw
        assert '\\"has_safety_issue\\"' not in raw
        assert "prompt_template" not in raw
        assert "system_prompt" not in raw
        assert "missing_information" not in raw

    def test_model_cost_and_latency_come_from_the_ledger(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-projection").json()

        understand = next(a for a in body["activities"] if a["id"] == "understand")
        assert understand["ai"]["executed"] == "claude-sonnet-4-5"
        assert understand["ai"]["requested"] == "auto"
        assert understand["ai"]["latency_ms"] == 1400

    def test_an_unknown_run_is_a_404(self, client):
        assert client.get("/api/runs/mine/does-not-exist/business-projection").status_code == 404


class TestTechnicalDetailEndpoint:
    def test_raw_output_is_available_only_here(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-technical/understand").json()

        assert body["title"] == "Request understood"
        node = body["nodes"][0]
        assert node["node_id"] == "understand_message"
        # This is the one place the model's own JSON surfaces.
        assert "raw" in node["output"]
        assert json.loads(node["output"]["raw"])["primary_intent"] == "RFQ"

    def test_it_shows_requested_alongside_executed(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-technical/understand").json()

        call = body["technical"]["ai_calls"][0]
        assert (call["requested"], call["executed"]) == ("auto", "claude-sonnet-4-5")

    def test_the_whole_run_can_be_inspected_at_once(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-technical/run").json()

        assert len(body["nodes"]) == 13

    def test_an_activity_this_run_never_performed_is_a_404(self, client):
        assert client.get(
            f"/api/runs/mine/{RUN_ID}/business-technical/deliverable"
        ).status_code == 404


class TestNarrationEndpoint:
    def test_it_narrates_once_and_serves_the_cache_thereafter(self, client, llm):
        before = llm.calls

        first = client.post(f"/api/runs/mine/{RUN_ID}/business-narration").json()
        assert first["cached"] is False
        assert llm.calls == before + 1

        second = client.post(f"/api/runs/mine/{RUN_ID}/business-narration").json()
        assert second["cached"] is True
        assert second["state_version"] == first["state_version"]
        # The identical state must not cost a second call (§17, §50).
        assert llm.calls == before + 1

    def test_the_projection_picks_the_cached_narration_up(self, client):
        client.post(f"/api/runs/mine/{RUN_ID}/business-narration")
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-projection").json()

        assert body["business_status"]["summary"] == "BASF SE needs a quotation."


class TestExplanationEndpoint:
    def test_it_returns_the_runs_own_facts_and_rules(self, client):
        body = client.get(f"/api/runs/mine/{RUN_ID}/business-explanation").json()

        assert body["decision"] == "Inside Sales"
        labels = {fact["label"] for fact in body["facts"]}
        # The evidence the routing rules actually read, not the extraction.
        assert {"Customer state", "Order state", "Primary department"} <= labels
        # The scripted model cites nothing, so the deterministic form survives.
        assert body["source"] == "deterministic"


class TestActionEndpoint:
    def test_an_action_the_projection_did_not_offer_is_forbidden(self, client):
        response = client.post(
            f"/api/runs/mine/{RUN_ID}/business-action",
            json={"type": "pause_run", "params": {}},
        )

        # This run is finished, so pausing was never offered.
        assert response.status_code == 403
        assert "not available" in response.json()["detail"]

    def test_an_unknown_action_type_is_forbidden_not_a_500(self, client):
        response = client.post(
            f"/api/runs/mine/{RUN_ID}/business-action",
            json={"type": "drop_database", "params": {}},
        )

        assert response.status_code == 403

    def test_an_offered_action_is_performed(self, client, db):
        response = client.post(
            f"/api/runs/mine/{RUN_ID}/business-action",
            json={"type": "add_note", "params": {"text": "Chased the datasheet."}},
        )

        assert response.status_code == 200
        assert response.json()["note"]["text"] == "Chased the datasheet."
        assert any(
            "business_notes" in update.get("$push", {})
            for _, update in db.run_history.updates
        )

    def test_a_route_override_is_recorded_as_a_persons_decision(self, client, db):
        response = client.post(
            f"/api/runs/mine/{RUN_ID}/business-action",
            json={"type": "route_override", "params": {"route": "Technical Sales", "reason": "Engineering input"}},
        )

        assert response.status_code == 200
        override = response.json()["override"]
        assert override["route"] == "Technical Sales"
        assert override["by"]
