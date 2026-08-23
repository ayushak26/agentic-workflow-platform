"""Durable, file-backed state used by the Workflow Builder.

Runtime workflow YAML remains in ``workflows/*.yaml``. Builder drafts and
immutable manual-save snapshots live under ``workflows/.builder`` so they do
not appear in the workflow library and cannot change execution semantics.
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

import yaml


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_DRAFT_BYTES = 5 * 1024 * 1024


def _utc_now() -> str:
    """Internal helper for the utc now step.

    Returns:
        str: The now.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _digest(text: str) -> str:
    """Internal helper for the digest step.

    Args:
        text (str): The text.

    Returns:
        str: The result.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    """Internal helper for the atomic write step.

    Args:
        path (Path): Filesystem path.
        text (str): The text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


class WorkflowBuilderStore:
    """Own autosave drafts and immutable versions for one workflows root."""

    def __init__(self, workflows_dir: Path):
        """Initialize the WorkflowBuilderStore.

        Args:
            workflows_dir (Path): The workflows dir.
        """
        self.workflows_dir = Path(workflows_dir)
        self.state_dir = self.workflows_dir / ".builder"

    @staticmethod
    def validate_name(name: str) -> str:
        """Validate the name.

        Args:
            name (str): Workflow or resource name.

        Returns:
            str: The name.
        """
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("name must be alphanumeric + _ -")
        return name

    @staticmethod
    def validate_version_id(version_id: str) -> str:
        """Validate the version id.

        Args:
            version_id (str): Version identifier.

        Returns:
            str: The version id.
        """
        if not _SAFE_VERSION.fullmatch(version_id):
            raise ValueError("invalid workflow version id")
        return version_id

    def workflow_path(self, name: str) -> Path:
        """Compute the workflow path.

        Args:
            name (str): Workflow or resource name.

        Returns:
            Path: The path.
        """
        return self.workflows_dir / f"{self.validate_name(name)}.yaml"

    def draft_path(self, name: str) -> Path:
        """Draft the path.

        Args:
            name (str): Workflow or resource name.

        Returns:
            Path: The path.
        """
        return self.state_dir / "drafts" / f"{self.validate_name(name)}.json"

    def versions_dir(self, name: str) -> Path:
        """Compute the versions dir.

        Args:
            name (str): Workflow or resource name.

        Returns:
            Path: The dir.
        """
        return self.state_dir / "versions" / self.validate_name(name)

    def save_draft(
        self,
        name: str,
        yaml_text: str,
        *,
        canvas: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save the draft.

        Args:
            name (str): Workflow or resource name.
            yaml_text (str): Workflow YAML text.
            canvas (dict[str, Any] | None): The canvas (optional, default None).

        Returns:
            dict[str, Any]: The draft.
        """
        if len(yaml_text.encode("utf-8")) > _MAX_DRAFT_BYTES:
            raise ValueError("workflow draft exceeds the 5 MB Builder limit")
        workflow_path = self.workflow_path(name)
        current_yaml = (
            workflow_path.read_text(encoding="utf-8")
            if workflow_path.exists()
            else ""
        )
        document = {
            "name": name,
            "updated_at": _utc_now(),
            "sha256": _digest(yaml_text),
            "base_sha256": _digest(current_yaml) if current_yaml else None,
            "yaml": yaml_text,
            "canvas": canvas or {},
        }
        _atomic_write(
            self.draft_path(name),
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        )
        return document

    def read_draft(self, name: str) -> dict[str, Any] | None:
        """Read the draft.

        Args:
            name (str): Workflow or resource name.

        Returns:
            dict[str, Any] | None: The draft.
        """
        path = self.draft_path(name)
        if not path.exists():
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        workflow_path = self.workflow_path(name)
        current_yaml = (
            workflow_path.read_text(encoding="utf-8")
            if workflow_path.exists()
            else ""
        )
        document["current_sha256"] = (
            _digest(current_yaml) if current_yaml else None
        )
        document["differs_from_current"] = (
            document.get("sha256") != document.get("current_sha256")
        )
        return document

    def delete_draft(self, name: str) -> bool:
        """Delete the draft.

        Args:
            name (str): Workflow or resource name.

        Returns:
            bool: The draft.
        """
        path = self.draft_path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def record_version(self, name: str, yaml_text: str) -> str:
        """Record the version.

        Args:
            name (str): Workflow or resource name.
            yaml_text (str): Workflow YAML text.

        Returns:
            str: The version.
        """
        digest = _digest(yaml_text)
        directory = self.versions_dir(name)
        existing = sorted(directory.glob(f"*-{digest[:12]}.yaml"))
        if existing:
            return existing[-1].stem
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        version_id = f"{stamp}-{digest[:12]}"
        _atomic_write(directory / f"{version_id}.yaml", yaml_text)
        return version_id

    def save_workflow(self, name: str, yaml_text: str) -> str:
        """Save the workflow.

        Args:
            name (str): Workflow or resource name.
            yaml_text (str): Workflow YAML text.

        Returns:
            str: The workflow.
        """
        path = self.workflow_path(name)
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if current != yaml_text:
                self.record_version(name, current)
        _atomic_write(path, yaml_text)
        version_id = self.record_version(name, yaml_text)
        self.delete_draft(name)
        return version_id

    def get_version(self, name: str, version_id: str) -> str:
        """Return the version.

        Args:
            name (str): Workflow or resource name.
            version_id (str): Version identifier.

        Returns:
            str: The version.
        """
        safe_version = self.validate_version_id(version_id)
        path = self.versions_dir(name) / f"{safe_version}.yaml"
        if not path.exists():
            raise FileNotFoundError(version_id)
        return path.read_text(encoding="utf-8")

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        """List the versions.

        Args:
            name (str): Workflow or resource name.

        Returns:
            list[dict[str, Any]]: The versions.
        """
        workflow_path = self.workflow_path(name)
        current_yaml = (
            workflow_path.read_text(encoding="utf-8")
            if workflow_path.exists()
            else ""
        )
        current_sha = _digest(current_yaml) if current_yaml else None
        versions: list[dict[str, Any]] = []
        directory = self.versions_dir(name)
        if not directory.exists():
            return versions
        for path in sorted(directory.glob("*.yaml"), reverse=True):
            text = path.read_text(encoding="utf-8")
            sha = _digest(text)
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                data = {}
            stamp = path.stem.split("-", 1)[0]
            try:
                created_at = datetime.strptime(
                    stamp,
                    "%Y%m%dT%H%M%S%fZ",
                ).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
            except ValueError:
                created_at = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=UTC,
                ).isoformat().replace("+00:00", "Z")
            versions.append({
                "version_id": path.stem,
                "created_at": created_at,
                "sha256": sha,
                "current": sha == current_sha,
                "workflow_version": data.get("version", "1.0"),
                "node_count": len(data.get("nodes", [])),
                "description": data.get("description", ""),
            })
        return versions

    def restore_version(self, name: str, version_id: str) -> tuple[str, str]:
        """Restore the version.

        Args:
            name (str): Workflow or resource name.
            version_id (str): Version identifier.

        Returns:
            tuple[str, str]: The version.
        """
        yaml_text = self.get_version(name, version_id)
        restored_version_id = self.save_workflow(name, yaml_text)
        return yaml_text, restored_version_id

    def delete_workflow(self, name: str) -> bool:
        """Remove the executable YAML together with every Builder-owned
        trace of it — an autosave draft and the whole immutable version
        history — so nothing orphaned lingers under ``.builder`` once the
        workflow itself is gone. Returns False if there was nothing to
        delete (the workflow didn't exist)."""
        path = self.workflow_path(name)
        existed = path.exists()
        if existed:
            path.unlink()
        self.delete_draft(name)
        versions_dir = self.versions_dir(name)
        if versions_dir.exists():
            shutil.rmtree(versions_dir)
        return existed
