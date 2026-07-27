"""Build complete copy/paste files and a path-preserving ZIP."""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


FILES = [
    ".env.example",
    "README.md",
    "WORKFLOW_FILE_INPUTS_IMPLEMENTATION.md",
    "app/api/runs.py",
    "app/api/workflow_files.py",
    "app/api/workflows.py",
    "app/config.py",
    "app/ingestion/extractor.py",
    "app/main.py",
    "app/nodes/__init__.py",
    "app/nodes/workflow_file_loader.py",
    "app/runtime/schema.py",
    "app/storage/minio_client.py",
    "app/workflow/file_inputs.py",
    "app/workflow/run_history.py",
    "scripts/build_workflow_file_inputs_bundle.py",
    "tests/test_workflow_file_inputs.py",
    "ui/src/api/client.ts",
    "ui/src/api/types.ts",
    "ui/src/modes/studio/Builder.tsx",
    "ui/src/modes/studio/RunDialog.tsx",
    "ui/src/modes/studio/RunHistory.tsx",
    "ui/src/modes/studio/WorkflowInputsPanel.tsx",
    "ui/src/modes/studio/yaml-bridge.ts",
    "workflows/file_input_demo.yaml",
]

LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


def build(repo: Path, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    missing = [relative for relative in FILES if not (repo / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"bundle files missing: {missing}")

    for relative in FILES:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, destination)

    manifest = output / "COPY_READY_FILE_LIST.txt"
    manifest.write_text("\n".join(FILES) + "\n", encoding="utf-8")

    sourcebook = output / "WORKFLOW_FILE_INPUTS_COPY_PASTE_ALL_FILES.md"
    with sourcebook.open("w", encoding="utf-8") as stream:
        stream.write(
            "# Workflow File Inputs - Complete Copy/Paste Files\n\n"
            "Each section contains one complete file. Copy it to the path "
            "shown relative to the repository root.\n\n"
        )
        for relative in FILES:
            language = LANGUAGE.get(Path(relative).suffix.lower(), "text")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sourcebook, archive = build(repo, args.output.resolve())
    print(sourcebook)
    print(archive)


if __name__ == "__main__":
    main()
