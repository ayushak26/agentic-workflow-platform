"""Ask AI about node types — explains what a node type does, when to use it,
and its advantages, grounded in the live node registry so it can never
describe a node type that doesn't exist and always reflects whatever node
types are currently registered (no hardcoded description file to go stale).
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.nodes.registry import NodeRegistry
from app.security.dependencies import CurrentUser, require_consultant

router = APIRouter(prefix="/api/node-types", tags=["node-types-chat"])

NODE_TYPE_CHAT_MODEL = "gpt-5.6-terra"
PROMPT_DRAFTING_MODEL = "gpt-5.6-luna"

_SYSTEM_PROMPT = (
    "You explain node types available in an agentic workflow builder to a "
    "non-technical audience — someone building a workflow who has never "
    "written code and doesn't know what a 'schema' or 'config field' is. "
    "Use ONLY the NODE TYPE CATALOG below as your source of truth — it is "
    "generated live from this platform's current node registry, so it is "
    "always complete and current — but NEVER surface its technical "
    "vocabulary in your answer. Specifically:\n"
    "- Never say 'config field', 'schema', 'property', 'parameter', "
    "'input_schema'/'output_schema', 'category:' as a label, or list raw "
    "field names verbatim. Translate every one of those into a plain-"
    "English capability instead (e.g. instead of \"has a prompt_template "
    "config field\", say \"you can tell it what to write in your own "
    "words\").\n"
    "- Explain, in simple everyday language: what this node actually does "
    "(as an action a person would recognize, not an implementation "
    "detail), when someone building a workflow should reach for it, and "
    "why it's a good choice compared to similar options — without naming "
    "internal categories or class names as the reason.\n"
    "- Keep answers short, concrete, and example-driven. Prefer 2-4 short "
    "sentences or a short bullet list over a long technical writeup.\n"
    "- If asked about a node type that is NOT in the catalog below, say "
    "plainly that it does not exist in this platform yet — never invent or "
    "assume one exists."
)


def _schema_field_names(schema: dict[str, Any] | None) -> list[str]:
    """Internal helper for the schema field names step.

    Args:
        schema (dict[str, Any] | None): Schema definition.

    Returns:
        list[str]: The field names.
    """
    if not isinstance(schema, dict):
        return []
    return list((schema.get("properties") or {}).keys())


def _build_node_type_catalog() -> str:
    """Always reads NodeRegistry.manifest() fresh — the same live source
    GET /api/node-types serves — so a node type added to the codebase shows
    up here automatically with no other change needed."""
    lines = []
    for entry in NodeRegistry.manifest():
        config_fields = _schema_field_names(entry.get("config_schema"))
        contract = entry.get("contract") or {}
        lines.append(
            f"- {entry['type_name']} (category: {entry.get('category', 'Other')}): "
            f"{entry.get('description') or 'no description provided'}. "
            f"Config fields: {', '.join(config_fields) or 'none'}. "
            f"Accepts: {', '.join(contract.get('accepts') or ['state'])}. "
            f"Produces: {', '.join(contract.get('produces') or ['state'])}."
        )
    return "\n".join(lines)


def _manifest_entry(type_name: str) -> dict[str, Any] | None:
    """Live lookup — same freshness guarantee as _build_node_type_catalog."""
    for entry in NodeRegistry.manifest():
        if entry["type_name"] == type_name:
            return entry
    return None


class ChatMessage(BaseModel):
    """Pydantic model defining the ChatMessage shape.

    Attributes:
        role (str).
        content (str).
    """
    role: str
    content: str


class AskContext(BaseModel):
    """Compact, structured context for a scoped Ask AI question — what the
    user actually clicked, not the whole workflow. See app/api/workflow_generation.py's
    docstring and the Builder's FeatureHelp/AskAiDialog components for the
    callers. Every field is optional: the fewer that are set, the more this
    degenerates back to the general "browse all node types" behaviour.
    """
    feature: str | None = None
    # Static copy the frontend already owns (ui/src/modes/studio/builder/
    # feature-help.ts) — passed through rather than duplicated server-side,
    # so there is exactly one place that copy is authored.
    feature_description: str | None = None
    node_type: str | None = None
    node_id: str | None = None
    field: str | None = None
    relevant_upstream_nodes: list[str] = []
    relevant_downstream_nodes: list[str] = []


class AskAboutNodeTypesRequest(BaseModel):
    """Pydantic model defining the AskAboutNodeTypesRequest shape.

    Attributes:
        question (str).
        focus_type_name (str | None).
        history (list[ChatMessage]).
        context (AskContext | None).
    """
    question: str
    focus_type_name: str | None = None
    history: list[ChatMessage] = []
    context: AskContext | None = None


def _compact_catalog(type_names: list[str]) -> str:
    """Same one-line-per-type shape as _build_node_type_catalog(), but for an
    explicit shortlist rather than the whole registry — what a node- or
    field-scoped question actually needs."""
    lines = []
    for name in type_names:
        entry = _manifest_entry(name)
        if entry is None:
            continue
        config_fields = _schema_field_names(entry.get("config_schema"))
        lines.append(
            f"- {entry['type_name']} (category: {entry.get('category', 'Other')}): "
            f"{entry.get('description') or 'no description provided'}. "
            f"Config fields: {', '.join(config_fields) or 'none'}."
        )
    return "\n".join(lines)


@router.post("/ask")
async def ask_about_node_types(
    req: AskAboutNodeTypesRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Compute the ask about node types.

    Args:
        req (AskAboutNodeTypesRequest): The req.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="node_types_ask",
            ledger=services.get("cost_ledger"),
            workflow_name="AI assist · node types",
        )

    context = req.context
    node_type = (context.node_type if context else None) or req.focus_type_name
    upstream = list(context.relevant_upstream_nodes) if context else []
    downstream = list(context.relevant_downstream_nodes) if context else []
    # De-duplicated, order-preserving shortlist of every node type this
    # question is actually scoped to.
    scoped_types = list(dict.fromkeys([t for t in [node_type, *upstream, *downstream] if t]))

    sections: list[str] = []
    if scoped_types:
        # Scoped question (a specific node, or its immediate neighbours) —
        # only those manifest entries are relevant, not all ~49 node types.
        sections.append(f"RELEVANT NODE TYPES (not the full catalog — this question is scoped):\n{_compact_catalog(scoped_types)}")
    else:
        sections.append(f"NODE TYPE CATALOG:\n{_build_node_type_catalog()}")

    if context and context.feature:
        label = context.feature_description or context.feature
        sections.append(f"The user is asking about this Builder feature ({context.feature}): {label}")
    if node_type:
        sections.append(f"The user is currently focused on: {node_type}")
    if context and context.node_id:
        sections.append(f"The specific step on the canvas has id {context.node_id!r}.")
    if context and context.field:
        sections.append(f"They are specifically looking at the configuration field {context.field!r}.")
    if upstream:
        sections.append(f"Steps immediately upstream of it: {', '.join(upstream)}.")
    if downstream:
        sections.append(f"Steps immediately downstream of it: {', '.join(downstream)}.")

    conversation = "\n".join(f"{m.role}: {m.content}" for m in req.history)
    if conversation:
        sections.append(f"CONVERSATION SO FAR:\n{conversation}")
    sections.append(f"QUESTION: {req.question}")

    user_prompt = "\n\n".join(sections)

    response = await llm.complete(
        model=NODE_TYPE_CHAT_MODEL,
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.2,
    )
    return {"answer": response.text}


_PROMPT_DRAFTING_SYSTEM_PROMPT = """# System Prompt: Expert Prompt Architect

You are an expert prompt architect. Your job is to transform a user's goal, rough idea, or existing prompt into a clear, precise, effective prompt that produces consistently high-quality results from an AI model.

## Core Objective

Create prompts that are:

* Clear and unambiguous
* Goal-oriented
* Context-aware
* Specific about requirements and constraints
* Structured for reliable execution
* Efficient without unnecessary instructions
* Adapted to the user's desired audience, tone, depth, and output format

## Method

When creating a prompt, determine the following:

1. **Objective**

   * What exactly should the AI accomplish?
   * What does a successful result look like?

2. **Context**

   * What background information does the AI need?
   * What assumptions should or should not be made?

3. **Role**

   * Assign the AI a role only when the role genuinely improves the result.
   * Prefer specific expertise over vague descriptions such as "world-class expert."

4. **Inputs**

   * Clearly identify information the user will provide.
   * Use placeholders such as `[TOPIC]`, `[AUDIENCE]`, `[DATA]`, or `[CONSTRAINTS]` when appropriate.

5. **Instructions**

   * Break complex work into logical steps.
   * State important requirements explicitly.
   * Prioritize instructions when some requirements are more important than others.

6. **Constraints**

   * Specify relevant limits such as length, scope, tone, sources, exclusions, budget, timeframe, or level of technical depth.
   * Do not add arbitrary constraints that do not improve the result.

7. **Output Format**

   * Define exactly how the final answer should be organized when structure matters.
   * Specify headings, bullets, tables, JSON, sections, or other formats only when useful.

8. **Quality Criteria**

   * Tell the AI what characteristics distinguish an excellent response.
   * Require accuracy, relevance, completeness, practical usefulness, and appropriate reasoning for the task.

9. **Uncertainty**

   * Do not invent missing facts.
   * If missing information materially affects the result, ask concise clarification questions.
   * If the missing information is minor, make a reasonable assumption and clearly state it when necessary.

## Prompt Design Principles

* Put the most important instructions first.
* Use concrete language instead of vague commands.
* Avoid redundant instructions.
* Avoid unnecessary persona language.
* Separate context, task, constraints, and output requirements.
* Use examples only when they materially improve interpretation.
* Never request hidden chain-of-thought or private reasoning.
* When reasoning is important, ask for concise explanations, calculations, evidence, assumptions, or decision criteria instead.
* Do not over-engineer simple tasks.
* Match prompt complexity to task complexity.
* Preserve all important requirements from the user's original request.
* Resolve conflicting requirements by prioritizing the user's explicit objective.

## Default Prompt Structure

When appropriate, construct the final prompt using this structure:

### Role

Who the AI should act as, if a specialized role is useful.

### Objective

The exact outcome to produce.

### Context

Relevant background information.

### Input

Information or material that will be supplied.

### Instructions

Specific steps and requirements for completing the task.

### Constraints

Important limitations and boundaries.

### Output Format

The required structure of the response.

### Quality Criteria

Standards the final result should satisfy.

## Interaction Rules

If the user's request is already sufficiently clear, do not ask unnecessary questions. Create the improved prompt immediately.

If critical information is missing, ask no more than the minimum number of questions needed to create a substantially better prompt.

If the user provides an existing prompt, preserve its intent while improving clarity, structure, precision, and reliability.

If multiple prompting approaches could work, choose the strongest default approach unless the user explicitly asks for alternatives.

## Final Response

Return:

1. **Optimized Prompt** — a ready-to-copy prompt the user can use immediately.
2. **Optional Variables** — placeholders the user can customize, only if relevant.
3. **Brief Notes** — only when useful, explain any important assumptions or design choices.

The optimized prompt itself should be self-contained and should not require the user to understand prompt-engineering terminology.

## Output Contract for This Integration

This response is inserted directly into a configuration field, not shown to the user as a document. Ignore the numbered "Optimized Prompt / Optional Variables / Brief Notes" structure above — instead, respond with ONLY the drafted prompt text itself: no heading, no Optional Variables section, no Brief Notes, no markdown fences, no quotes around it, no commentary. Just the ready-to-paste prompt text, exactly as it should appear in the field. If the request is too vague to draft something useful, ask one concise clarifying question instead of guessing."""


class DraftPromptRequest(BaseModel):
    """Pydantic model defining the DraftPromptRequest shape.

    Attributes:
        type_name (str).
        field_name (str).
        instruction (str).
        history (list[ChatMessage]).
    """
    type_name: str
    field_name: str
    instruction: str
    history: list[ChatMessage] = []


@router.post("/draft-prompt")
async def draft_prompt(
    req: DraftPromptRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Draft the prompt.

    Args:
        req (DraftPromptRequest): The req.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    entry = _manifest_entry(req.type_name)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Node type {req.type_name!r} does not exist in this platform's current registry.",
        )

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="draft_prompt",
            ledger=services.get("cost_ledger"),
            workflow_name="AI assist · node types",
        )

    conversation = "\n".join(f"{m.role}: {m.content}" for m in req.history)
    user_prompt = (
        f"Node type: {req.type_name} (category: {entry.get('category', 'Other')})\n"
        f"What this node type does: {entry.get('description') or 'no description provided'}\n"
        f"Field being edited: {req.field_name}\n"
        + (f"\nCONVERSATION SO FAR:\n{conversation}\n" if conversation else "")
        + f"\nUSER REQUEST: {req.instruction}"
    )

    response = await llm.complete(
        model=PROMPT_DRAFTING_MODEL,
        system=_PROMPT_DRAFTING_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.4,
    )
    return {"answer": response.text}


_INSTRUCTIONS_DRAFTING_SYSTEM_PROMPT = """You write the Instructions text for one step of an automated business workflow. This text is the ONLY thing that tells the AI model what to do — there is no separate system prompt the user manages, and the model never sees raw template syntax like `{{inputs.x}}`. You are given the step's declared Inputs (the information it receives) and Outputs (the information it must produce), and sometimes the Instructions text a person already wrote.

## When there are no existing instructions

Write clear, complete instructions from scratch, grounded only in the given Inputs and Outputs:
- Explain what to do with the inputs in plain business language.
- For every output field, explain how to decide its value. For an enum/fixed-set field, give the disambiguation rule a person would actually need — the realistic edge case where two plausible values could both seem to fit, and which one wins and why (reason from the field's own name, description and allowed values; do not invent business facts the fields don't imply).
- Instruct the model to never invent a value that is not stated or strongly implied by the inputs — when something is missing, it should say so (empty value, or the closest catch-all allowed value) rather than guess.
- Do not mention config fields, JSON, schemas, or `{{...}}` template syntax anywhere — write only about what the inputs mean and what the outputs should contain, in the language a business colleague would use.

## When existing instructions are given

Treat them as the source of truth for business logic. You MUST preserve, unchanged in meaning:
- every business rule and classification distinction already stated,
- every worked example (e.g. "X should be classified as A, not B, because..."),
- every restriction, exception and "never do this" instruction.
Rewrite only for clarity, structure and concision — remove repetition, tighten wording, reorganize for readability. Add instructions only for Inputs or Outputs the existing text doesn't yet address, inferring what's needed the same way as the from-scratch case above. Never silently drop or reverse a business rule, and never add a new business rule the fields don't imply just to sound more thorough.

## Output contract for this integration

Respond with ONLY the drafted instructions text: no heading, no preamble like "Here are the instructions", no markdown fences, no commentary about what you changed. Just the ready-to-use instructions text, exactly as it should appear in the field."""


class DraftInstructionsFieldSpec(BaseModel):
    """Pydantic model defining the DraftInstructionsFieldSpec shape.

    Attributes:
        name (str).
        description (str).
        type (str | None).
        enum_values (list[str]).
    """
    name: str
    description: str = ""
    type: str | None = None
    enum_values: list[str] = []


class DraftInstructionsRequest(BaseModel):
    """Pydantic model defining the DraftInstructionsRequest shape.

    Attributes:
        existing_instructions (str).
        input_fields (list[DraftInstructionsFieldSpec]).
        output_fields (list[DraftInstructionsFieldSpec]).
    """
    existing_instructions: str = ""
    input_fields: list[DraftInstructionsFieldSpec] = []
    output_fields: list[DraftInstructionsFieldSpec] = []


def _describe_fields(fields: list[DraftInstructionsFieldSpec]) -> str:
    """Internal helper for the describe fields step.

    Args:
        fields (list[DraftInstructionsFieldSpec]): Field names.

    Returns:
        str: The fields.
    """
    if not fields:
        return "(none declared yet)"
    lines = []
    for field in fields:
        bits = [field.type] if field.type else []
        if field.enum_values:
            bits.append("one of: " + ", ".join(field.enum_values))
        suffix = f" ({'; '.join(bits)})" if bits else ""
        description = f" — {field.description}" if field.description else ""
        lines.append(f"- {field.name}{suffix}{description}")
    return "\n".join(lines)


@router.post("/draft-instructions")
async def draft_instructions(
    req: DraftInstructionsRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Draft the instructions.

    Args:
        req (DraftInstructionsRequest): The req.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="draft_instructions",
            ledger=services.get("cost_ledger"),
            workflow_name="AI assist · node types",
        )

    sections = [
        f"INPUTS this step receives:\n{_describe_fields(req.input_fields)}",
        f"OUTPUTS this step must produce:\n{_describe_fields(req.output_fields)}",
    ]
    if req.existing_instructions.strip():
        sections.append(
            "EXISTING INSTRUCTIONS (preserve their business logic; improve "
            f"clarity):\n{req.existing_instructions.strip()}"
        )
    else:
        sections.append("No existing instructions — write these from scratch.")
    user_prompt = "\n\n".join(sections)

    response = await llm.complete(
        model=PROMPT_DRAFTING_MODEL,
        system=_INSTRUCTIONS_DRAFTING_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.4,
    )
    return {"answer": response.text}


_CODE_DRAFTING_SYSTEM_PROMPT = """You write one short code snippet for a step of an automated business workflow — either a Python snippet (PythonSnippetAgent) or a read-only SQL SELECT (SQLQueryAgent). You are given the step's declared inputs and outputs, sometimes example values for them, and sometimes code a person already wrote to refine.

Rules specific to each language:

PYTHON: The snippet runs in an isolated sandbox with `inputs` (a dict) already bound — read values as `inputs["name"]`, never `import` anything of this platform's own, never assume network or filesystem access beyond what stdlib gives you in-memory. Write results into the `output` dict, e.g. `output["total"] = ...` — one assignment per declared output field. Do not write `import os`, `import sys`, or attempt any file/network/subprocess access; none of it works in the sandbox and it will just look like a mistake.

SQL: Write exactly one SELECT statement, never more than one statement, never a write (no INSERT/UPDATE/DELETE/CREATE/ALTER/DROP). Use named placeholders in the form %(name)s for any value that should come from params — never inline a value directly into the query text.

## Output contract for this integration

Respond with ONLY the code: no heading, no preamble, no markdown fences (no ```), no commentary about what you changed or why. Just the ready-to-use code, exactly as it should appear in the field."""


class DraftCodeRequest(BaseModel):
    """Pydantic model defining the DraftCodeRequest shape.

    Attributes:
        language (Literal['python', 'sql']).
        existing_code (str).
        input_fields (list[DraftInstructionsFieldSpec]).
        output_fields (list[DraftInstructionsFieldSpec]).
        example_inputs (dict[str, Any]).
        example_outputs (dict[str, Any]).
        instructions (str).
    """
    language: Literal["python", "sql"]
    existing_code: str = ""
    input_fields: list[DraftInstructionsFieldSpec] = []
    output_fields: list[DraftInstructionsFieldSpec] = []
    #: Optional concrete example values — few-shot grounding, not just field
    #: names/types. `Any`-typed since an example value can legitimately be
    #: a number, string, list, or object.
    example_inputs: dict[str, Any] = {}
    example_outputs: dict[str, Any] = {}
    instructions: str = ""


@router.post("/draft-code")
async def draft_code(
    req: DraftCodeRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Draft the code.

    Args:
        req (DraftCodeRequest): The req.
        request (Request): Incoming FastAPI request.
        user (CurrentUser): Authenticated current user (optional, default Depends(require_consultant)).
    """
    services = request.app.state.services
    llm = services.get("llm")
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM gateway unavailable")

    scope = getattr(user, "session_id", None) or user.username
    if hasattr(llm, "with_context"):
        llm = llm.with_context(
            run_id="node-types-chat", session_id=scope, node_id="draft_code",
            ledger=services.get("cost_ledger"),
            workflow_name="AI assist · node types",
        )

    sections = [f"LANGUAGE: {req.language}"]
    if req.instructions.strip():
        sections.append(f"WHAT THIS STEP SHOULD DO:\n{req.instructions.strip()}")
    sections.append(f"INPUTS available:\n{_describe_fields(req.input_fields)}")
    sections.append(f"OUTPUTS required:\n{_describe_fields(req.output_fields)}")
    if req.example_inputs:
        sections.append(f"EXAMPLE INPUT VALUES:\n{json.dumps(req.example_inputs, indent=2, default=str)}")
    if req.example_outputs:
        sections.append(f"EXAMPLE OUTPUT VALUES (what a correct run should produce):\n{json.dumps(req.example_outputs, indent=2, default=str)}")
    if req.existing_code.strip():
        sections.append(
            f"EXISTING CODE (preserve its intent; fix or improve it):\n{req.existing_code.strip()}"
        )
    else:
        sections.append("No existing code — write it from scratch.")
    user_prompt = "\n\n".join(sections)

    response = await llm.complete(
        model=PROMPT_DRAFTING_MODEL,
        system=_CODE_DRAFTING_SYSTEM_PROMPT,
        user=user_prompt,
        temperature=0.2,
    )
    return {"answer": response.text}
