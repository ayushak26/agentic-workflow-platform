"""Run a workflow with a tracing subscriber. Prints every event as it fires.

Usage (from repo root):
    python scripts/smoke_events.py

What you should see:
    [node_started     ] node=lit
    [node_completed   ] node=lit                   preview={'echoed': 'hi'}
    [node_started     ] node=echo
    [node_completed   ] node=echo                  preview={'echoed': 'hi'}
    [run_completed    ] node=-
    === Final: completed (run_id=...) ===

The sequence and timing should match what you'd see in the Cockpit when 9B
wires up. If you see anything different, Option A's wiring is broken.
"""
import asyncio
import sys
from pathlib import Path

# Allow running as `python scripts/smoke_events.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime.events import RunEvent, RunEventBus
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string


HELLO_YAML = """
name: smoke
description: smoke
version: "1.0"
inputs:
  who:
    type: text
nodes:
  - id: lit
    type: Literal
    config:
      value: "hello"
  - id: echo
    type: Echo
    config:
      template: "world"
edges:
  - from: lit
    to: echo
"""


class _MinimalLLM:
    """Placeholder so services dict has the key. Unused for Literal+Echo."""
    async def complete(self, *a, **kw): return {"text": ""}
    async def complete_structured(self, *a, **kw): return {}
    async def chat_with_tools(self, *a, **kw): return {"text": "", "tool_calls": []}


async def main() -> None:
    bus = RunEventBus()

    # Trace every publish. This is a debugging seam, not production code —
    # the production WS endpoint subscribes via bus.subscribe() normally.
    real_publish = bus.publish

    async def trace(evt: RunEvent) -> None:
        line = f"[{evt.type:18s}] node={evt.node_id or '-':<10s}"
        if evt.output_preview:
            line += f"  preview={evt.output_preview[:80]}"
        if evt.error:
            line += f"  error={evt.error[:80]}"
        print(line, flush=True)
        await real_publish(evt)

    bus.publish = trace

    services = {"llm": _MinimalLLM(), "event_bus": bus}
    spec = load_workflow_from_string(HELLO_YAML)
    result = await run_workflow(spec, {"text": "hi"}, services=services)

    print(f"\n=== Final: {result['status']} (run_id={result['run_id']}) ===")


if __name__ == "__main__":
    asyncio.run(main())