"""Bounded, session-scoped pub/sub for workflow Server-Sent Events.

The transport is intentionally one-way: workflow state changes flow from the
API to the browser over standard HTTP. Human decisions continue to use
authenticated REST endpoints. A bounded replay buffer lets an SSE client
reconnect with Last-Event-ID without losing node status transitions.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

EventType = Literal[
    "node_started",
    "node_completed",
    "node_reused",
    "node_paused",
    "run_completed",
    "run_rejected",
    "run_failed",
    "model_selected",
    "llm_token",
]
_TERMINAL_EVENTS = {"run_completed", "run_rejected", "run_failed"}


@dataclass
class RunEvent:
    type: EventType
    run_id: str
    session_id: str | None = None
    node_id: str | None = None
    output_preview: str | None = None
    context: dict[str, Any] | None = None
    error: str | None = None
    ts: str = ""
    event_id: int | None = None
    output_preview: str | None = None
    token: str | None = None
    context: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()

    @property
    def terminal(self) -> bool:
        return self.type in _TERMINAL_EVENTS

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("session_id", None)
        return {
            key: value
            for key, value in data.items()
            if value is not None
        }


class RunEventBus:
    def __init__(
        self,
        *,
        max_events_per_run: int = 1000,
        max_run_histories: int = 1000,
    ) -> None:
        self._subscribers: dict[
            tuple[str, str],
            list[asyncio.Queue[RunEvent]],
        ] = {}
        self._history: OrderedDict[
            tuple[str, str],
            deque[RunEvent],
        ] = OrderedDict()
        self._next_event_id: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()
        self._max_events_per_run = max(1, max_events_per_run)
        self._max_run_histories = max(1, max_run_histories)

    @staticmethod
    def _key(run_id: str, session_id: str | None) -> tuple[str, str]:
        return (session_id or "", run_id)

    async def publish(self, evt: RunEvent) -> None:
        key = self._key(evt.run_id, evt.session_id)
        async with self._lock:
            next_id = self._next_event_id.get(key, 0) + 1
            self._next_event_id[key] = next_id
            evt.event_id = next_id

            history = self._history.get(key)
            if history is None:
                while len(self._history) >= self._max_run_histories:
                    old_key, _ = self._history.popitem(last=False)
                    self._next_event_id.pop(old_key, None)
                history = deque(maxlen=self._max_events_per_run)
                self._history[key] = history
            else:
                self._history.move_to_end(key)
            history.append(evt)
            queues = list(self._subscribers.get(key, ()))

        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(evt)

    async def subscribe(
        self,
        run_id: str,
        session_id: str | None = None,
        *,
        after_event_id: int | None = None,
    ) -> asyncio.Queue[RunEvent]:
        key = self._key(run_id, session_id)
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(
            maxsize=self._max_events_per_run
        )
        async with self._lock:
            replay = tuple(self._history.get(key, ()))
            self._subscribers.setdefault(key, []).append(queue)

        cutoff = after_event_id or 0
        for event in replay:
            if (event.event_id or 0) > cutoff:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(event)
        return queue

    async def unsubscribe(
        self,
        run_id: str,
        queue: asyncio.Queue[RunEvent],
        session_id: str | None = None,
    ) -> None:
        key = self._key(run_id, session_id)
        async with self._lock:
            subscribers = self._subscribers.get(key)
            if not subscribers:
                return
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(key, None)


# Singleton retained for scripts that import it directly. The FastAPI lifespan
# creates the configured instance used by the application.
bus = RunEventBus()
