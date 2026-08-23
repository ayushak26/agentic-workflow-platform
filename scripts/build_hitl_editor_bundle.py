"""Build complete copy/paste files and a path-preserving HITL editor ZIP."""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


FILES = [
    "HITL_EDITOR_IMPLEMENTATION.md",
    "app/api/workflow_files.py",
    "app/api/workflows.py",
    "app/nodes/human_in_loop.py",
    "app/runtime/compiler.py",
    "app/workflow/file_inputs.py",
    "scripts/build_hitl_editor_bundle.py",
    "tests/test_hitl_agent.py",
    "tests/test_workflow_file_inputs.py",
    "ui/src/api/client.ts",
    "ui/src/api/types.ts",
    "ui/src/index.css",
    "ui/src/modes/studio/Cockpit.tsx",
    "ui/src/modes/studio/HITLPanel.tsx",
    "ui/src/modes/studio/RichTextEditor.tsx",
    "ui/src/modes/studio/RunDialog.tsx",
]

LANGUAGE = {
    ".css": "css",
    ".md": "markdown",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
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
    missing = [relative for relative in FILES if not (repo / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"bundle files missing: {missing}")

    for relative in FILES:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, destination)

    manifest = output / "COPY_READY_FILE_LIST.txt"
    manifest.write_text("\n".join(FILES) + "\n", encoding="utf-8")

    sourcebook = output / "HITL_EDITOR_COPY_PASTE_ALL_FILES.md"
    with sourcebook.open("w", encoding="utf-8") as stream:
        stream.write(
            "# Human-in-the-Loop Editor - Complete Copy/Paste Files\n\n"
            "Each section contains one complete file. Copy it to the path "
            "shown relative to the repository root.\n\n"
        )
        for relative in FILES:
            language = LANGUAGE.get(Path(relative).suffix.lower(), "text")
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
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sourcebook, archive = build(repo, args.output.resolve())
    print(sourcebook)
    print(archive)


if __name__ == "__main__":
    main()
