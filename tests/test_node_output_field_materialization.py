"""Regression coverage for the "declared output field absent at runtime" bug class.

Preflight authorises a template reference when the field name exists on the
target node's ``output_schema``, but at runtime templates resolve against the
raw dict ``run()`` returned. A field declared WITH A DEFAULT and skipped on
some code path therefore passed preflight and then died mid-run with
``KeyError: Template path not resolvable`` — and only on that branch, so it
never surfaced in ordinary testing.

Found by auditing all 43 registered node types for ``run()`` methods whose
return paths carry different key sets. Three did: RAGAgent
(``grounding_for_drafter`` omitted on the "no sources matched" early return),
KimiVisionAgent (``input_tokens``/``output_tokens`` on its skip path), and
OpenAIImageGenerationAgent (five fields on its skip path). RAGAgent was a
LIVE bug: ``horizon_v4.yaml`` references ``{{rag_N_N.grounding_for_drafter}}``
from six drafter nodes, all of which would fail whenever retrieval returned
no chunks (an empty/sparse index, or an off-target query).

The fix materialises declared defaults into node_outputs in
app/runtime/compiler.py, so the whole class is closed rather than the three
instances being patched individually.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from langgraph.checkpoint.memory import MemorySaver

import app.nodes  # noqa: F401 - populates the registry
from app.llm.base import LLMResponse
from app.nodes.registry import NodeRegistry
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string


class _StubLLM:
    async def complete(self, **kwargs):
        return LLMResponse(text="x", model="m", input_tokens=1, output_tokens=1)


class _EmptyRetrievalResult:
    """The `not result.chunks` branch of RAGAgent.run()."""

    chunks: list = []
    rewritten_query = "q"


async def _empty_retriever(query, llm=None):
    return _EmptyRetrievalResult()


RAG_WORKFLOW = """
name: rag_grounding_materialization
entry: rag_step
nodes:
  - id: rag_step
    type: RAGAgent
    config:
      model: claude-opus-5
      query: anything
  - id: drafter
    type: Echo
    config:
      template: "GROUNDING:[{{rag_step.grounding_for_drafter}}]"
edges:
  - from: rag_step
    to: drafter
exit: drafter
"""


async def test_rag_empty_retrieval_still_resolves_grounding_template():
    """The live horizon_v4.yaml bug: six drafters read
    {{rag_N_N.grounding_for_drafter}}, which RAGAgent omits when retrieval
    returns nothing."""
    spec = load_workflow_from_string(RAG_WORKFLOW)
    services = {
        "llm": _StubLLM(),
        "retriever": _empty_retriever,
        "cost_ledger": None,
        "langgraph_checkpointer": MemorySaver(),
    }
    result = await run_workflow(
        spec, {}, services=services, run_id="rag-materialize-1"
    )

    assert result["status"] == "completed"
    outputs = result["state"]["node_outputs"]
    assert "grounding_for_drafter" in outputs["rag_step"]
    # Rendered rather than raising KeyError mid-run.
    assert outputs["drafter"]["text"] == "GROUNDING:[]"


async def test_values_actually_returned_by_the_node_are_never_overwritten():
    """The merge must be strictly additive — a real value always wins over
    the schema default, otherwise materialisation would silently corrupt
    output."""
    spec = load_workflow_from_string(RAG_WORKFLOW)
    services = {
        "llm": _StubLLM(),
        "retriever": _empty_retriever,
        "cost_ledger": None,
        "langgraph_checkpointer": MemorySaver(),
    }
    result = await run_workflow(
        spec, {}, services=services, run_id="rag-materialize-2"
    )
    rag_out = result["state"]["node_outputs"]["rag_step"]
    # `answer` IS returned by the early-return path, with this exact text.
    assert rag_out["answer"] == "No sources matched the query."
    assert rag_out["rewritten_query"] == "q"


EXTRA_KEY_WORKFLOW = """
name: extra_key_preservation
entry: emitter
nodes:
  - id: emitter
    type: Literal
    config:
      value: hello
  - id: reader
    type: Echo
    config:
      template: "V={{emitter.value}}"
edges:
  - from: emitter
    to: reader
exit: reader
"""


async def test_declared_fields_round_trip_unchanged():
    spec = load_workflow_from_string(EXTRA_KEY_WORKFLOW)
    services = {"langgraph_checkpointer": MemorySaver()}
    result = await run_workflow(
        spec, {}, services=services, run_id="extra-key-1"
    )
    assert result["status"] == "completed"
    assert result["state"]["node_outputs"]["reader"]["text"] == "V=hello"


class TestNoNodeTypeHasAnUnmaterialisableOutputField:
    """Static guard so a future node type can't silently reintroduce this.

    Any ``run()`` whose return paths disagree about which keys they include
    is exactly the shape that caused the bug. That is now SAFE (the compiler
    materialises declared defaults), so this test does not forbid it — it
    asserts the compensating invariant instead: every such field must be
    declared on the node's output_schema, because only declared fields get a
    default materialised. A field omitted on one path AND absent from the
    schema would still be unresolvable at runtime.
    """

    @staticmethod
    def _inconsistent_return_fields(cls) -> set[str]:
        module = __import__(cls.__module__, fromlist=["x"])
        path = pathlib.Path(module.__file__)
        tree = ast.parse(path.read_text())
        for class_def in [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == cls.__name__
        ]:
            for fn in [
                n for n in class_def.body
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                and n.name == "run"
            ]:
                keysets = []
                for node in ast.walk(fn):
                    if isinstance(node, ast.Return) and isinstance(
                        node.value, ast.Dict
                    ):
                        keys = set()
                        has_star = False
                        for key in node.value.keys:
                            if key is None:
                                has_star = True
                                continue
                            if isinstance(key, ast.Constant) and isinstance(
                                key.value, str
                            ):
                                keys.add(key.value)
                        keysets.append((keys, has_star))
                if len(keysets) < 2:
                    continue
                union = set().union(*[k for k, _ in keysets])
                return {
                    field
                    for field in union
                    if any(field not in k and not s for k, s in keysets)
                }
        return set()

    @pytest.mark.parametrize(
        "type_name", sorted(NodeRegistry._registry)
    )
    def test_path_dependent_output_fields_are_schema_declared(self, type_name):
        cls = NodeRegistry.get(type_name)
        schema = getattr(cls, "output_schema", None)
        if schema is None:
            pytest.skip("no output_schema")
        inconsistent = self._inconsistent_return_fields(cls)
        undeclared = inconsistent - set(schema.model_fields)
        assert not undeclared, (
            f"{type_name}.run() omits {sorted(undeclared)} on at least one "
            "return path, and they are NOT declared on its output_schema — so "
            "the compiler cannot materialise a default and a template "
            "referencing them will fail at runtime with 'Template path not "
            "resolvable'. Declare them on the output_schema (with a default) "
            "or return them on every path."
        )
