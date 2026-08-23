"""Build the copy-ready sourcebook and ZIP for this implementation."""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


FILES = [
    ".env.example",
    "EU_PROPOSAL_REASONING_IMPLEMENTATION.md",
    "app/api/proposals.py",
    "app/api/runs.py",
    "app/api/workflows.py",
    "app/config.py",
    "app/llm/anthropic_gw.py",
    "app/llm/openai_gw.py",
    "app/llm/registry.py",
    "app/main.py",
    "app/mcp/server.py",
    "app/nodes/__init__.py",
    "app/nodes/call_coverage.py",
    "app/nodes/claim_evidence_verifier.py",
    "app/nodes/concept_alternatives.py",
    "app/nodes/evidence_agent.py",
    "app/nodes/horizon_evaluation.py",
    "app/nodes/human_in_loop.py",
    "app/nodes/router.py",
    "app/observability/cost_ledger.py",
    "app/observability/metrics.py",
    "app/proposal_graph/concepts.py",
    "app/proposal_graph/coverage.py",
    "app/proposal_graph/evidence_verification.py",
    "app/proposal_graph/graph.py",
    "app/proposal_graph/horizon_evaluator.py",
    "app/proposal_graph/models.py",
    "app/proposal_graph/workspace_store.py",
    "app/runtime/compiler.py",
    "app/runtime/executor.py",
    "app/runtime/hitl.py",
    "app/workflow/run_history.py",
    "scripts/build_eu_proposal_copy_bundle.py",
    "tests/test_durable_hitl.py",
    "tests/test_evidence_verification.py",
    "tests/test_llm_resilience.py",
    "tests/test_proposal_reasoning.py",
    "tests/test_proposal_workspace_store.py",
    "ui/src/api/client.ts",
    "ui/src/api/types.ts",
    "ui/src/modes/studio/ProposalReview.tsx",
    "ui/src/modes/studio/RunHistory.tsx",
    "ui/src/modes/studio/StudioLayout.tsx",
    "ui/src/modes/studio/StudioRoot.tsx",
    "workflows/eu_proposal_evidence_pipeline.yaml",
]

LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".example": "dotenv",
}


def build(repo: Path, output: Path) -> tuple[Path, Path]:
    """Build the result.

    Args:
        repo (Path): The repo.
        output (Path): Node output mapping.

    Returns:
        tuple[Path, Path]: The result.
    """
    output.mkdir(parents=True, exist_ok=True)
    missing = [item for item in FILES if not (repo / item).is_file()]
    if missing:
        raise FileNotFoundError(f"bundle files missing: {missing}")

    for relative in FILES:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, destination)

    manifest = output / "COPY_READY_FILE_LIST.txt"
    manifest.write_text("\n".join(FILES) + "\n", encoding="utf-8")

    sourcebook = output / (
        "Eurskem_EU_Proposal_Reasoning_COPY_PASTE_ALL_FILES.md"
    )
    with sourcebook.open("w", encoding="utf-8") as stream:
        stream.write(
            "# Eurskem EU Proposal Reasoning - Complete Copy/Paste Files\n\n"
            "Copy each complete file to the path shown relative to the "
            "repository root.\n\n"
        )
        for relative in FILES:
            suffix = Path(relative).suffix.lower()
            language = LANGUAGE.get(suffix, "text")
            if relative == ".env.example":
                language = "dotenv"
            content = (repo / relative).read_text(encoding="utf-8")
            stream.write(f"## `{relative}`\n\n")
            stream.write(f"````{language}\n{content.rstrip()}\n````\n\n")

    archive = output.parent / f"{output.name}.zip"
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    return sourcebook, archive


def main() -> None:
    """Compute the main."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output",
        type=Path,
        help="Directory that will contain the copy-ready tree.",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sourcebook, archive = build(repo, args.output.resolve())
    print(sourcebook)
    print(archive)


if __name__ == "__main__":
    main()
