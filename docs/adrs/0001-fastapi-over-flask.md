# ADR 0001: FastAPI over Flask

## Status
Accepted — 2026-05-20

## Context
A common pattern in agentic platform proposals is to specify Flask as the backend framework. This build deviates to FastAPI. The deviation needs to be defensible.

## Decision
Use FastAPI 0.115+ with uvicorn (uvloop + httptools).

## Consequences

### Positives
- **Async-native.** LangGraph parallel branches (5 concurrent section drafters in the flagship workflow) map cleanly onto async/await. Flask requires gevent monkey-patching or a threadpool, both of which add operational surface.
- **WebSockets are first-class.** The Cockpit UI streams live node status over a single FastAPI WebSocket endpoint. In Flask we would need flask-sock or Flask-SocketIO with their own quirks.
- **Pydantic typing end to end.** Node input/output schemas double as request and response schemas. OpenAPI is generated for free.
- **OpenAPI for free.** `/docs` and `/openapi.json` ship out of the box — enables the spec discipline from Mastering API Architecture without bolting on a separate library.

### Negatives / risks
- **Smaller ecosystem of mature plugins** vs Flask. Acceptable: we don't need the long tail.
- **Async-everywhere discipline.** A sync call inside an async handler blocks the event loop. We mitigate with explicit async clients (httpx, motor, etc.) and code review.

## Alternatives considered
- **Flask** — rejected for the async + WebSocket reasons above.
- **FastAPI + ASGI sync routes** (mix-and-match) — rejected because the mental model of "everything is async" is simpler to defend than "mostly async."

## Reference
Mastering API Architecture — ADR Guidelines pattern (Introduction, Table I-2).
