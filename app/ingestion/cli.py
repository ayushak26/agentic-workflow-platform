"""CLI entry point for ingestion. Invoked via `make ingest FILE=path`.

Parses args, calls the async pipeline, prints a summary. Errors propagate
with non-zero exit code so Make and CI can detect failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.ingestion.pipeline import ingest_file
from app.observability.logging import configure_logging


def _parse_metadata(raw: list[str]) -> dict[str, str]:
    """Parse repeated --meta key=value flags into a dict."""
    out: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"--meta must be key=value, got: {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


async def _amain(args: argparse.Namespace) -> None:
    path = Path(args.file)
    metadata = _parse_metadata(args.meta)

    result = await ingest_file(path, metadata=metadata)

    summary = {
        "minio_key": result.minio_key,
        "chunk_count": result.chunk_count,
        "status": result.status,
        "skipped": result.skipped,
        "chunk_ids_sample": result.chunk_ids[:3],
    }
    print(json.dumps(summary, indent=2))
    from app.db.mongo import get_mongo_client
    await get_mongo_client().close()


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Ingest a document into the platform.",
    )
    parser.add_argument("file", help="Path to the document to ingest")
    parser.add_argument(
        "--meta",
        action="append",
        default=[],
        help="Document metadata as key=value (repeatable). e.g. --meta industry=mining",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_amain(args))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ingestion failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()