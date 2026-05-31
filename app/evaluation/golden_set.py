"""Load a golden set from JSONL. One JSON object per line."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class GoldenExample(BaseModel):
    id: str
    question: str
    context: str       # the labelled sources [1]..[N] the answer should ground in
    reference: str     # the gold answer


def load_golden_set(path: str | Path) -> list[GoldenExample]:
    p = Path(path)
    examples: list[GoldenExample] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        examples.append(GoldenExample.model_validate_json(line))
    return examples