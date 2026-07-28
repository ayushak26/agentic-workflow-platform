"""Workflow run events with Redis fan-out and an offline in-process fallback.

Architectural note for interviews:
- Production passes a Redis client, so WebSocket/SSE subscribers receive events
  even when the HTTP request and workflow run land on different Uvicorn workers.
- Tests and offline scripts omit Redis and retain the same asyncio.Queue API.
"""
from __future__ import annotations
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Literal

EventType = Literal[
    "node_started",
    "node_completed",
    "node_reused",
    "node_paused",
    "model_selected",
    "llm_token",
    "run_completed",
    "run_failed",
]

@dataclass
class RunEvent:
    type: EventType
    run_id: str
    node_id: str | None = None
    output_preview: str | None = None
    context: dict[str, Any] | None = None
    token: str | None = None
    error: str | None = None
    ts: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["ts"]:
            d["ts"] = datetime.now(timezone.utc).isoformat()
        return {k: v for k, v in d.items() if v is not None}


class RunEventBus:
    def __init__(self, redis=None) -> None:
        self._redis = redis
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._redis_subscriptions: dict[
            int,
            tuple[asyncio.Task, Any],
        ] = {}

    async def publish(self, evt: RunEvent) -> None:
        if self._redis is not None:
            await self._redis.publish(
                self._channel(evt.run_id),
                json.dumps(evt.to_json(), separators=(",", ":")),
            )
            return
        async with self._lock:
            queues = list(self._subscribers.get(evt.run_id, []))
        for q in queues:
            await q.put(evt)

    async def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        q: asyncio.Queue[RunEvent] = asyncio.Queue()
        if self._redis is not None:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(self._channel(run_id))
            task = asyncio.create_task(
                self._forward_redis(pubsub, q),
                name=f"run-events:{run_id}",
            )
            self._redis_subscriptions[id(q)] = (task, pubsub)
            return q
        async with self._lock:
            self._subscribers[run_id].append(q)
        return q

    async def unsubscribe(self, run_id: str, q: asyncio.Queue[RunEvent]) -> None:
        subscription = self._redis_subscriptions.pop(id(q), None)
        if subscription is not None:
            task, pubsub = subscription
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await pubsub.unsubscribe(self._channel(run_id))
            await pubsub.aclose()
            return
        async with self._lock:
            if q in self._subscribers.get(run_id, []):
                self._subscribers[run_id].remove(q)

    async def _forward_redis(self, pubsub, queue: asyncio.Queue[RunEvent]) -> None:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if not message:
                await asyncio.sleep(0)
                continue
            raw = message.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            await queue.put(RunEvent(**payload))

    @staticmethod
    def _channel(run_id: str) -> str:
        return f"awp:run-events:{run_id}"


# Singleton wired into the FastAPI services dict in main.py
bus = RunEventBus()
