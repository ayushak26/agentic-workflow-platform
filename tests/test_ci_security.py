from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_workflow(name: str) -> dict:
    # PyYAML 1.1 treats the key "on" as a boolean; the assertions below do not
    # depend on that key and GitHub itself uses YAML 1.2.
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / name).read_text()
    )


def test_normal_ci_has_read_only_permissions_and_no_provider_secrets():
    path = ROOT / ".github" / "workflows" / "ci.yml"
    raw = path.read_text()
    workflow = load_workflow("ci.yml")

    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets.OPENAI_API_KEY" not in raw
    assert "secrets.ANTHROPIC_API_KEY" not in raw
    assert "OPENAI_API_KEY:" not in raw
    assert "ANTHROPIC_API_KEY:" not in raw
    assert "persist-credentials: false" in raw


def test_live_keys_are_confined_to_manual_protected_workflow():
    raw = (
        ROOT / ".github" / "workflows" / "live-llm-tests.yml"
    ).read_text()

    assert "workflow_dispatch:" in raw
    assert "pull_request:" not in raw
    assert "push:" not in raw
    assert "environment: live-llm-tests" in raw
    assert "github.ref == 'refs/heads/main'" in raw
    assert "persist-credentials: false" in raw
    assert "secrets.OPENAI_API_KEY" in raw
    assert "secrets.ANTHROPIC_API_KEY" in raw


def test_commonly_leaked_secret_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text()

    for pattern in (
        ".env*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "secrets/",
        "credentials/",
    ):
        assert pattern in ignored


def test_ionos_deploy_is_gated_and_uses_verified_ssh_transport():
    raw = (ROOT / ".github" / "workflows" / "deploy-ionos.yml").read_text()

    assert "workflow_run:" in raw
    assert "conclusion == 'success'" in raw
    assert "head_branch == 'main'" in raw
    assert "environment: production" in raw
    assert "persist-credentials: false" in raw
    assert "DEPLOY_KNOWN_HOSTS" in raw
    assert "StrictHostKeyChecking=no" not in raw
    assert "sha256sum -c" in raw
