"""Bounded, session-scoped pub/sub for workflow Server-Sent Events.

The transport is intentionally one-way: workflow state changes flow from the
API to the browser over standard HTTP. Human decisions continue to use
authenticated REST endpoints. A bounded replay buffer lets an SSE client
reconnect with Last-Event-ID without losing node status transitions.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
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
    token: str | None = None
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
        redis: Any | None = None,
        max_events_per_run: int = 1000,
        max_run_histories: int = 1000,
        replay_ttl_seconds: int = 86_400,
    ) -> None:
        self._redis = redis
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
        self._replay_ttl_seconds = max(60, replay_ttl_seconds)
        self._redis_subscriptions: dict[
            int,
            tuple[Any, asyncio.Task[None]],
        ] = {}

    @staticmethod
    def _key(run_id: str, session_id: str | None) -> tuple[str, str]:
        return (session_id or "", run_id)

    async def publish(self, evt: RunEvent) -> None:
        if self._redis is not None:
            await self._publish_redis(evt)
            return

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

    @staticmethod
    def _redis_names(run_id: str, session_id: str | None) -> tuple[str, str, str]:
        """Return opaque, tenant-scoped Redis keys without leaking identifiers."""

        digest = hashlib.sha256(
            f"{session_id or ''}\0{run_id}".encode("utf-8")
        ).hexdigest()
        prefix = f"awp:run-events:{digest}"
        return f"{prefix}:stream", f"{prefix}:sequence", f"{prefix}:channel"

    async def _publish_redis(self, evt: RunEvent) -> None:
        stream, sequence, channel = self._redis_names(
            evt.run_id,
            evt.session_id,
        )
        evt.event_id = int(await self._redis.incr(sequence))
        payload = json.dumps(asdict(evt), ensure_ascii=False, separators=(",", ":"))
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.xadd(
            stream,
            {"event": payload},
            maxlen=self._max_events_per_run,
            approximate=True,
        )
        pipeline.expire(stream, self._replay_ttl_seconds)
        pipeline.expire(sequence, self._replay_ttl_seconds)
        pipeline.publish(channel, payload)
        await pipeline.execute()

    async def subscribe(
        self,
        run_id: str,
        session_id: str | None = None,
        *,
        after_event_id: int | None = None,
    ) -> asyncio.Queue[RunEvent]:
        if self._redis is not None:
            return await self._subscribe_redis(
                run_id,
                session_id,
                after_event_id=after_event_id,
            )

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

    async def _subscribe_redis(
        self,
        run_id: str,
        session_id: str | None,
        *,
        after_event_id: int | None,
    ) -> asyncio.Queue[RunEvent]:
        stream, _, channel = self._redis_names(run_id, session_id)
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(
            maxsize=self._max_events_per_run
        )
        pubsub = self._redis.pubsub()

        # Subscribe before replaying. Redis buffers channel messages while the
        # bounded stream is read, and the pump de-duplicates by event_id. This
        # closes the otherwise easy replay/live race between two Uvicorn workers.
        await pubsub.subscribe(channel)
        cutoff = after_event_id or 0
        history = await self._redis.xrange(stream, min="-", max="+")
        last_seen = cutoff
        for _, fields in history:
            event = self._decode_redis_event(fields)
            event_id = event.event_id or 0
            if event_id <= cutoff:
                continue
            self._offer(queue, event)
            last_seen = max(last_seen, event_id)

        task = asyncio.create_task(
            self._pump_redis(pubsub, queue, last_seen),
            name=f"run-events:{run_id}",
        )
        self._redis_subscriptions[id(queue)] = (pubsub, task)
        return queue

    @staticmethod
    def _decode_redis_event(fields: dict[Any, Any]) -> RunEvent:
        payload = fields.get("event")
        if payload is None:
            payload = fields.get(b"event")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise ValueError("Redis run-event stream entry has no event payload")
        return RunEvent(**json.loads(payload))

    @staticmethod
    def _offer(queue: asyncio.Queue[RunEvent], event: RunEvent) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)

    async def _pump_redis(
        self,
        pubsub: Any,
        queue: asyncio.Queue[RunEvent],
        last_seen: int,
    ) -> None:
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not message or message.get("type") != "message":
                    await asyncio.sleep(0)
                    continue
                payload = message.get("data")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8")
                event = RunEvent(**json.loads(payload))
                event_id = event.event_id or 0
                if event_id <= last_seen:
                    continue
                last_seen = event_id
                self._offer(queue, event)
        except asyncio.CancelledError:
            raise

    async def unsubscribe(
        self,
        run_id: str,
        queue: asyncio.Queue[RunEvent],
        session_id: str | None = None,
    ) -> None:
        if self._redis is not None:
            subscription = self._redis_subscriptions.pop(id(queue), None)
            if subscription is None:
                return
            pubsub, task = subscription
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            return

        key = self._key(run_id, session_id)
        async with self._lock:
            subscribers = self._subscribers.get(key)
            if not subscribers:
                return
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(key, None)

    async def close(self) -> None:
        """Stop Redis subscription pumps before the shared client is closed."""

        subscriptions = list(self._redis_subscriptions.values())
        self._redis_subscriptions.clear()
        for pubsub, task in subscriptions:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


# Singleton retained for scripts that import it directly. The FastAPI lifespan
# creates the configured instance used by the application.
bus = RunEventBus()