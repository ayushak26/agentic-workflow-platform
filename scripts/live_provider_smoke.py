"""Explicit, low-cost provider smoke test for the protected manual CI job."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm.anthropic_gw import AnthropicGateway


async def smoke_anthropic() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is required in the protected "
            "live-llm-tests environment"
        )

    gateway = AnthropicGateway(api_key=api_key)
    response = await gateway.complete(
        model=os.environ.get("LIVE_ANTHROPIC_MODEL", "claude-haiku-4-5"),
        system="You are a health-check endpoint.",
        user="Reply with OK.",
        temperature=0.0,
        max_tokens=16,
    )
    if not response.text.strip():
        raise RuntimeError("Anthropic returned an empty response")
    print(
        "Anthropic smoke test passed",
        {
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        choices=("anthropic",),
        required=True,
    )
    args = parser.parse_args()

    if args.provider == "anthropic":
        asyncio.run(smoke_anthropic())


if __name__ == "__main__":
    main()
