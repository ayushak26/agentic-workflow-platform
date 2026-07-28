"""Authenticated 100-concurrent-user release gate with no LLM calls."""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.security.jwt_handler import create_access_token
from app.security.rbac import Role


async def main_async(args: argparse.Namespace) -> None:
    host = settings.allowed_hosts[0] if settings.allowed_hosts else "localhost"
    semaphore = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=10,
        headers={"Host": host},
    ) as client:
        async def one(index: int) -> tuple[int, float]:
            token = create_access_token(
                {
                    "sub": f"load-user-{index}",
                    "role": Role.CONSULTANT.value,
                    "session_id": f"load-user-{index}",
                }
            )
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(
                    "/api/workflows",
                    headers={"Authorization": f"Bearer {token}"},
                )
                return response.status_code, time.perf_counter() - started

        results = await asyncio.gather(*(one(i) for i in range(args.requests)))

    statuses = [status for status, _ in results]
    latencies = sorted(latency for _, latency in results)
    failures = sum(status != 200 for status in statuses)
    p95_index = max(0, round(0.95 * len(latencies)) - 1)
    p95 = latencies[p95_index]
    mean = statistics.fmean(latencies)
    print(
        f"Load gate: {len(results)} requests, {failures} failures, "
        f"mean={mean:.3f}s, p95={p95:.3f}s"
    )
    if failures:
        raise SystemExit(f"Load gate failed with statuses: {sorted(set(statuses))}")
    if p95 > args.max_p95_seconds:
        raise SystemExit(
            f"Load gate p95 {p95:.3f}s exceeds {args.max_p95_seconds:.3f}s"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--max-p95-seconds", type=float, default=2.0)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
