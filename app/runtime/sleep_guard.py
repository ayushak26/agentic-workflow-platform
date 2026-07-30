"""macOS-only guard against idle sleep while a workflow is executing.

System sleep is OS-level process suspension: it freezes every process's CPU
time, timers, and network sockets, not something specific to uvicorn or this
app. Nothing running inside the process can make a run survive it, and a
laptop that sleeps mid-run will almost certainly kill whatever LLM connection
was in flight. Rather than trying to recover from sleep, this stops it from
starting in the first place while work is in flight, using the same
mechanism Terminal/`caffeinate` uses.

Reference-counted so concurrent runs share one `caffeinate` process; sleep is
allowed again once the last active run releases it.

Caveat this cannot work around: `caffeinate` only blocks *idle* sleep.
Closing the lid forces sleep regardless of any assertion a user process
holds — that is a macOS power-management decision made below the level any
process (this one included) can override.
"""
from __future__ import annotations

import asyncio
import os
import platform
import subprocess

from app.observability.logging import get_logger

logger = get_logger(__name__)

_lock = asyncio.Lock()
_process: subprocess.Popen | None = None
_active_runs = 0


def _enabled() -> bool:
    # Skip under pytest regardless of platform so the suite never spawns real
    # subprocesses just because it happens to run on a Mac.
    return platform.system() == "Darwin" and "PYTEST_CURRENT_TEST" not in os.environ


async def acquire() -> None:
    """Block idle sleep for the duration of one workflow run."""

    global _process, _active_runs
    if not _enabled():
        return
    async with _lock:
        _active_runs += 1
        if _process is None:
            try:
                _process = subprocess.Popen(
                    ["caffeinate", "-im"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info("sleep_guard.engaged")
            except FileNotFoundError:
                logger.warning("sleep_guard.caffeinate_not_found")


async def release() -> None:
    """Release this run's hold; sleep is allowed again once none remain."""

    global _process, _active_runs
    if not _enabled():
        return
    async with _lock:
        _active_runs = max(0, _active_runs - 1)
        if _active_runs == 0 and _process is not None:
            _process.terminate()
            _process = None
            logger.info("sleep_guard.released")
