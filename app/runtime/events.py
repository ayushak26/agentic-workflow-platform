"""In-process pub/sub for workflow run events.

Architectural note for interviews:
- This is a single-process bus using asyncio.Queue. It is sufficient for the local
  Docker Compose build and any single-container deployment.
- The upgrade path is one swap: replace _subscribers/publish with Redis pub/sub
  (channel = f"run:{run_id}"). The bus interface stays the same. Redis is already
  in our locked stack for the cache layer.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Literal

EventType = Literal[
    "node_started", "node_completed", "node_paused", "run_completed", "run_failed"
]

@dataclass
class RunEvent:
    type: EventType
    run_id: str
    node_id: str | None = None
    output_preview: str | None = None
    context: dict[str, Any] | None = None
    error: str | None = None
    ts: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["ts"]:
            d["ts"] = datetime.utcnow().isoformat() + "Z"
        return {k: v for k, v in d.items() if v is not None}


class RunEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, evt: RunEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(evt.run_id, []))
        for q in queues:
            await q.put(evt)

    async def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        q: asyncio.Queue[RunEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[run_id].append(q)
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue[RunEvent]) -> None:
        async with self._lock:
            if q in self._subscribers.get(run_id, []):
                self._subscribers[run_id].remove(q)


# Singleton wired into the FastAPI services dict in main.py
bus = RunEventBus()