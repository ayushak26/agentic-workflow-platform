"""Golden set loading.

A golden set is a JSONL file: one JSON object per line, each a known
(question, context, reference) tuple. Fields match the on-disk format in
eval/golden_set/*.jsonl exactly — change one, change the other.

`reference` is the ideal answer. It is used in-memory to guide the judge and is
never persisted with scorecards (it's an evaluation aid, not output).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class GoldenExample(BaseModel):
    """Pydantic model defining the GoldenExample shape.

    Attributes:
        id (str).
        question (str).
        context (str).
        reference (str).
    """
    id: str
    question: str
    context: str
    reference: str = ""


def load_golden_set(path: str | Path) -> list[GoldenExample]:
    """Load a JSONL golden set. Skips blank lines; fails loud on malformed rows."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Golden set not found: {p}")

    examples: list[GoldenExample] = []
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{lineno} is not valid JSON: {e}") from e
            examples.append(GoldenExample(**obj))
    return examples