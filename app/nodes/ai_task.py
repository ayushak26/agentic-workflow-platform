"""AITaskAgent — one configurable AI capability.

This node exists so that "extract structured information from a German email",
"classify a request into six intents", and "draft a reply in the customer's
language" are three *configurations*, not three node types. Everything that
usually motivates a new agent class — the prompt, the output schema, the label
set, the language, the model — is config here.

    ┌────────────┐   task + instruction + visual schema + language policy
    │  AI Task   │ ◀── all configuration, no code
    └────────────┘
          │
          ├─ result       the validated structured object (typed contract)
          ├─ status       ok | refused | invalid_output | provider_error
          ├─ confidence   surfaced separately so a Decision node can gate on it
          └─ text         free-text output for non-structured tasks

Structured output is enforced through the provider's native mechanism (via the
gateway's ``complete_structured``), not by asking for JSON in the prompt. The
four outcomes in §7 are handled distinctly: a schema-valid response, a model
refusal, a validation failure after retries, and a provider failure are
different facts about the run and are reported as different statuses rather than
collapsed into one exception.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.errors import (
    LLMProviderUnavailableError,
    StructuredOutputError,
)
from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.observability.logging import get_logger
from app.runtime.field_schema import (
    FieldSpec,
    build_response_model,
    describe_schema,
    field_paths,
    parse_fields,
)

log = get_logger(__name__)


AITaskKind = Literal[
    "extract",
    "classify",
    "summarize",
    "translate",
    "analyze",
    "rewrite",
    "generate",
    "draft_response",
    "compare",
    "evaluate",
    "custom",
]

#: Task-specific framing prepended to the author's own instruction. This is the
#: only place task semantics live; adding a task is a line here, not a class.
TASK_DIRECTIVES: dict[str, str] = {
    "extract": (
        "Extract structured information from the supplied content. Record only "
        "what the content states explicitly or implies unambiguously. When a "
        "value is not present, return null (or an empty list) for it — never "
        "guess, infer a plausible value, or copy a similar-looking value from "
        "elsewhere in the content."
    ),
    "classify": (
        "Classify the supplied content using only the allowed values in the "
        "output schema. If the content genuinely fits no allowed value, choose "
        "the closest catch-all value and lower your reported confidence."
    ),
    "summarize": (
        "Summarize the supplied content faithfully. Add no facts, "
        "recommendations, or conclusions that the content does not contain."
    ),
    "translate": (
        "Translate the supplied content. Preserve meaning, technical terms, "
        "product names, part numbers and figures exactly; do not localise units "
        "or identifiers."
    ),
    "analyze": (
        "Analyze the supplied content and report your findings. Ground every "
        "statement in the content and state plainly when the content is "
        "insufficient to support a conclusion."
    ),
    "rewrite": (
        "Rewrite the supplied content as instructed while preserving its factual "
        "content. Do not introduce new claims."
    ),
    "generate": (
        "Generate the requested content following the instruction precisely."
    ),
    "draft_response": (
        "Draft a reply to the supplied communication. Write only what the "
        "available information supports: do not commit to prices, dates, "
        "availability, or technical suitability that is not given to you. Where "
        "information is missing, ask for it."
    ),
    "compare": (
        "Compare the supplied items on the stated criteria. Report differences "
        "that are evidenced in the content and note where a comparison cannot "
        "be made."
    ),
    "evaluate": (
        "Evaluate the supplied content against the stated criteria and report a "
        "judgement with its reasoning."
    ),
    "custom": "",
}

#: Presets shown in the Builder's task picker (§32). A preset chooses a task and
#: a starting instruction — it never becomes a separate backend node type.
TASK_PRESETS: list[dict[str, Any]] = [
    {
        "id": "structured_extraction",
        "label": "Structured Extraction",
        "task": "extract",
        "summary": "Turn unstructured content into a typed business object.",
        "instruction": (
            "Read the incoming communication and extract the information "
            "described by the output schema.\n\n"
            "Extract only information that is explicitly stated or "
            "unambiguously implied. Do not invent missing values.\n"
            "List anything a colleague would still need to act on the request "
            "in missing_information."
        ),
        "include_confidence": True,
    },
    {
        "id": "classification",
        "label": "Classification",
        "task": "classify",
        "summary": "Assign one label from a fixed set, with a confidence score.",
        "instruction": (
            "Classify the content into exactly one of the allowed values.\n"
            "Report how certain you are; lower the score when the content is "
            "ambiguous or incomplete."
        ),
        "include_confidence": True,
    },
    {
        "id": "translation",
        "label": "Translation",
        "task": "translate",
        "summary": "Translate content, preserving identifiers and figures.",
        "instruction": "Translate the content into the configured output language.",
        "include_confidence": False,
    },
    {
        "id": "summarization",
        "label": "Summarization",
        "task": "summarize",
        "summary": "Condense content without adding anything to it.",
        "instruction": (
            "Summarize the content for a colleague who has not seen it. Keep "
            "every commitment, figure, deadline and identifier."
        ),
        "include_confidence": False,
    },
    {
        "id": "draft_response",
        "label": "Draft Response",
        "task": "draft_response",
        "summary": "Draft a reply for a human to review before it is sent.",
        "instruction": (
            "Draft a professional reply to the customer.\n"
            "Confirm what we understood, ask for anything missing, and commit "
            "to nothing that is not stated in the information you were given."
        ),
        "include_confidence": False,
    },
    {
        "id": "custom",
        "label": "Custom",
        "task": "custom",
        "summary": "Write the instruction yourself.",
        "instruction": "",
        "include_confidence": False,
    },
]


class LanguagePolicy(BaseModel):
    """Multilingual behaviour, configured on the AI Task rather than modelled as
    separate translation nodes (§8).

    One call detects the language, reasons over the original text, and returns a
    normalised business representation — which is both cheaper and more accurate
    than translate-then-extract, because translation loses exactly the technical
    identifiers extraction depends on.
    """

    model_config = ConfigDict(extra="forbid")

    input_language: str = "auto"
    process_in_original_language: bool = True
    output_language: str = "en"
    preserve_original: bool = True

    def directive(self) -> str:
        lines: list[str] = []
        if self.input_language == "auto":
            lines.append(
                "The content may be in any language, and may mix languages. "
                "Detect the language yourself."
            )
        else:
            lines.append(f"The content is written in {self.input_language}.")
        if self.process_in_original_language:
            lines.append(
                "Reason over the original text. Do not translate it before "
                "understanding it — technical terms, product names and "
                "identifiers must be read as written."
            )
        if self.output_language and self.output_language != "source":
            lines.append(
                f"Write your output in {self.output_language}, except for values "
                "that must stay verbatim: product names, model designations, "
                "serial and part numbers, quantities, units and proper nouns."
            )
        else:
            lines.append("Write your output in the language of the content.")
        if self.preserve_original:
            lines.append(
                "Where the schema asks for original or quoted text, reproduce "
                "the source wording exactly rather than a translation."
            )
        return "\n".join(lines)


class AITaskExample(BaseModel):
    """A few-shot example. Output is free-form so the same field works for both
    structured and text tasks; it is serialised as JSON when it is an object."""

    model_config = ConfigDict(extra="forbid")

    input: str
    output: Any = None
    note: str = ""


class AITaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: AITaskKind = Field(
        default="extract",
        description="What kind of AI capability this step performs — extract, classify, summarize, translate, draft a reply, etc.",
    )
    instruction: str = Field(
        default="",
        description="The task in your own words — what you want the model to do with the input.",
    )
    #: The content the task operates on. Normally a template reference such as
    #: {{inputs.message}} or {{outputs.previous.text}} — the Builder's mapping
    #: picker writes it.
    input: str = Field(
        default="",
        description="What this step reads — normally a reference to an earlier step's output, written by the mapping picker.",
    )
    #: Extra labelled context blocks, each templated. Keeps the main `input`
    #: readable when a task needs several sources.
    context: dict[str, str] = Field(
        default_factory=dict,
        description="Extra labelled context blocks (each templated) to keep the main input readable when the task needs several sources.",
    )
    model: str = Field(
        default="auto",
        description="Which language model runs this step. 'auto' lets the platform route to the best fit for the task.",
    )
    output_fields: list[FieldSpec] = Field(
        default_factory=list,
        description="The structured output shape this step must return — required for extract/classify tasks.",
    )
    language: LanguagePolicy = Field(
        default_factory=LanguagePolicy,
        description="Which language the model should reply in, or whether to match the input's language.",
    )
    examples: list[AITaskExample] = Field(
        default_factory=list,
        description="Optional worked examples shown to the model to steer its output format and tone.",
    )
    #: Adds a numeric `confidence` and a `reasoning` field to the contract
    #: without the author having to remember to. Confidence is what makes
    #: uncertainty routable (§12).
    include_confidence: bool = Field(
        default=True,
        description="Adds a confidence score and reasoning to the output, so a Decision/Router step can route uncertain answers to a human.",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Higher values make the model's answer more varied; 0 is the most consistent.")
    max_tokens: int = Field(default=8192, ge=256, description="Upper bound on how long the model's answer may be.")
    max_retries: int = Field(default=1, ge=0, le=3, description="How many times to retry if the model's output fails to validate against the output schema.")
    #: When false, a refusal/invalid output/provider failure is reported as a
    #: status instead of raising. Downstream Decision/Router nodes can then
    #: route the failure to a human rather than killing the run — which is the
    #: behaviour a business process usually wants.
    fail_on_error: bool = Field(
        default=True,
        description="When off, a model failure becomes a routable status instead of stopping the run — lets a Decision/Router step send it to a human instead.",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Optional reasoning-effort override for models that support it (low/medium/high).",
    )

    @model_validator(mode="after")
    def structured_tasks_declare_a_schema(self) -> "AITaskConfig":
        if self.task in ("extract", "classify") and not self.output_fields:
            raise ValueError(
                f"task {self.task!r} needs an output schema — add at least one "
                "field in the Structured Output builder"
            )
        return self


class AITaskInput(BaseModel):
    pass


class AITaskOutput(BaseModel):
    """The typed contract every downstream node sees.

    `result` is the author's own schema. It is kept under one key rather than
    spread across the output so that renaming a schema field can never collide
    with a runtime field like `status`.
    """

    result: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    status: Literal["ok", "refused", "invalid_output", "provider_error"] = "ok"
    error: str | None = None
    confidence: float | None = None
    reasoning: str | None = None
    detected_language: str | None = None
    model_used: str | None = None
    attempts: int = 0


def _confidence_fields(fields: list[FieldSpec]) -> list[FieldSpec]:
    declared = {field.name for field in fields}
    generated: list[FieldSpec] = []
    if "confidence" not in declared:
        generated.append(
            FieldSpec(
                name="confidence",
                type="number",
                description=(
                    "How certain you are about this result, 0.0 to 1.0. Lower it "
                    "when the content is ambiguous, incomplete, or outside your "
                    "competence."
                ),
                required=True,
                minimum=0.0,
                maximum=1.0,
            )
        )
    if "reasoning" not in declared:
        generated.append(
            FieldSpec(
                name="reasoning",
                type="text",
                description=(
                    "One or two sentences on how you reached this result, and "
                    "what made you uncertain."
                ),
                required=False,
            )
        )
    return generated


def effective_fields(config: dict[str, Any]) -> list[FieldSpec]:
    """The schema the model is actually held to, generated fields included.

    Shared by run(), preflight and the Builder's Outputs tab so all three agree
    on the node's contract.
    """
    fields = parse_fields(config.get("output_fields") or [])
    if not fields:
        return []
    if config.get("include_confidence", True):
        fields = [*fields, *_confidence_fields(fields)]
    return fields


@NodeRegistry.register
class AITaskAgent(NodeType):
    type_name = "AITaskAgent"
    description = (
        "One configurable AI step: extract, classify, summarize, translate, "
        "draft or analyze — with a typed structured-output contract."
    )
    input_schema = AITaskInput
    output_schema = AITaskOutput
    config_schema = AITaskConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "ai"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Sends content to a language model with a task, an instruction and a "
            "typed output schema, and returns a validated structured result."
        ),
        "why": (
            "Replaces per-purpose AI agents. The prompt, labels, schema, "
            "language policy and model are configuration, so a new business "
            "behaviour needs no new node type."
        ),
        "receives": "Text or structured content from workflow inputs or an upstream node.",
        "produces": (
            "result (your schema), plus status, confidence and detected "
            "language for downstream routing."
        ),
        "uses_ai": True,
        "external_action": False,
        "presets": TASK_PRESETS,
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return {"llm", "cost_ledger"}

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Authorise `result.<path>` references for the author's own schema.

        This is what makes a visually-built schema a real contract: preflight
        rejects `{{extract.result.custmoer.company}}` before a run starts,
        zero tokens spent, because the path index comes from the same rows the
        model is constrained by.
        """
        declared = set(AITaskOutput.model_fields)
        try:
            fields = effective_fields(config)
        except Exception:
            # A malformed schema is reported by config validation with a far
            # better message than anything this hook could produce; don't
            # double-report it as a template problem.
            return declared | {"result"}
        return (
            declared
            | {"result"}
            | {f"result.{item.path}" for item in field_paths(fields)}
        )

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        # With no schema declared, `result` is always {} — a bare reference to it
        # can only ever substitute an empty object, which is an authoring error
        # rather than a runtime surprise.
        if not (config.get("output_fields") or []):
            return {"result": {}}
        return {}

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    async def run(
        self, state, resolved_config: dict[str, Any]
    ) -> dict[str, Any]:
        cfg = AITaskConfig(**resolved_config)
        llm = self.services["llm"]
        fields = effective_fields(resolved_config)

        system = self._system_prompt(cfg, fields)
        user = self._user_prompt(cfg, fields)

        if not fields:
            return await self._run_text(cfg, llm, system, user)
        return await self._run_structured(cfg, llm, system, user, fields)

    # -- prompt construction -------------------------------------------

    def _system_prompt(self, cfg: AITaskConfig, fields: list[FieldSpec]) -> str:
        parts = [
            "You are a precise business-process assistant inside an automated "
            "workflow. Your output is consumed by deterministic business rules, "
            "not by a person reading prose.",
            TASK_DIRECTIVES.get(cfg.task, ""),
            cfg.language.directive(),
        ]
        if fields:
            parts.append(
                "Return every field of the required structure. The field "
                "descriptions below define what each one means:\n"
                + describe_schema(fields)
            )
            parts.append(
                "Never fabricate a value to fill a required field. Use null, an "
                "empty list, or the schema's catch-all value, and say so in your "
                "reasoning."
            )
        return "\n\n".join(part for part in parts if part.strip())

    def _user_prompt(self, cfg: AITaskConfig, fields: list[FieldSpec]) -> str:
        blocks: list[str] = []
        if cfg.instruction.strip():
            blocks.append(f"# Instruction\n{cfg.instruction.strip()}")
        for label, value in cfg.context.items():
            if str(value).strip():
                blocks.append(f"# {label}\n{value}")
        if cfg.examples:
            blocks.append(self._examples_block(cfg))
        content = cfg.input.strip()
        blocks.append(
            f"# Content\n{content}"
            if content
            else "# Content\n(no content was supplied for this step)"
        )
        return "\n\n".join(blocks)

    @staticmethod
    def _examples_block(cfg: AITaskConfig) -> str:
        rendered: list[str] = ["# Examples"]
        for index, example in enumerate(cfg.examples, start=1):
            output = (
                example.output
                if isinstance(example.output, str)
                else json.dumps(example.output, ensure_ascii=False, indent=2)
            )
            rendered.append(
                f"## Example {index}\nInput:\n{example.input}\n\n"
                f"Expected output:\n{output}"
                + (f"\n\nNote: {example.note}" if example.note else "")
            )
        return "\n\n".join(rendered)

    # -- free-text path ------------------------------------------------

    async def _run_text(
        self, cfg: AITaskConfig, llm: Any, system: str, user: str
    ) -> dict[str, Any]:
        try:
            response = await llm.complete(
                model=cfg.model,
                system=system,
                user=user,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        except LLMProviderUnavailableError as error:
            return self._failure(cfg, "provider_error", str(error), attempts=1)
        except Exception as error:  # provider/transport failure
            return self._failure(cfg, "provider_error", str(error), attempts=1)

        return {
            "result": {},
            "text": response.text,
            "status": "ok",
            "error": None,
            "confidence": None,
            "reasoning": None,
            "detected_language": None,
            "model_used": getattr(response, "model", cfg.model),
            "attempts": 1,
        }

    # -- structured path -----------------------------------------------

    async def _run_structured(
        self,
        cfg: AITaskConfig,
        llm: Any,
        system: str,
        user: str,
        fields: list[FieldSpec],
    ) -> dict[str, Any]:
        response_model = build_response_model(
            fields, model_name=f"{self.node_id}_Result"
        )
        prompt = user
        last_error: Exception | None = None
        attempts = cfg.max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                instance = await llm.complete_structured(
                    model=cfg.model,
                    system=system,
                    user=prompt,
                    response_model=response_model,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
            except LLMProviderUnavailableError as error:
                # No model can serve the request. Retrying the same prompt
                # cannot change that, so stop immediately rather than burning
                # the retry budget on a certain failure.
                log.error(
                    "ai_task.provider_unavailable",
                    node_id=self.node_id,
                    error=str(error),
                )
                return self._failure(
                    cfg, "provider_error", str(error), attempts=attempt
                )
            except StructuredOutputError as error:
                last_error = error
                status = "invalid_output"
            except Exception as error:
                last_error = error
                status = (
                    "refused"
                    if _looks_like_refusal(error)
                    else "invalid_output"
                )
            else:
                parsed = instance.model_dump(mode="python")
                return self._success(cfg, parsed, attempt)

            log.warning(
                "ai_task.structured_attempt_failed",
                node_id=self.node_id,
                attempt=attempt,
                total_attempts=attempts,
                status=status,
                error=str(last_error),
            )
            if attempt < attempts:
                prompt = (
                    f"{user}\n\n"
                    "# Correction required\n"
                    "Your previous response did not satisfy the required "
                    "structure. Return every field using its native JSON type: "
                    "objects as objects, lists as arrays, numbers unquoted. Use "
                    "null for values the content does not state.\n"
                    f"Validation error: {last_error}"
                )

        return self._failure(
            cfg,
            status,
            f"no schema-valid response after {attempts} attempt(s): {last_error}",
            attempts=attempts,
        )

    def _success(
        self,
        cfg: AITaskConfig,
        parsed: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        confidence = parsed.get("confidence")
        reasoning = parsed.get("reasoning")
        return {
            "result": parsed,
            "text": json.dumps(parsed, ensure_ascii=False, default=str),
            "status": "ok",
            "error": None,
            "confidence": (
                float(confidence) if isinstance(confidence, (int, float)) else None
            ),
            "reasoning": reasoning if isinstance(reasoning, str) else None,
            "detected_language": _detected_language(parsed),
            "model_used": cfg.model,
            "attempts": attempt,
        }

    def _failure(
        self, cfg: AITaskConfig, status: str, message: str, *, attempts: int
    ) -> dict[str, Any]:
        if cfg.fail_on_error:
            raise RuntimeError(
                f"AITaskAgent '{self.node_id}' failed ({status}): {message}"
            )
        # Confidence 0.0 rather than None: a confidence gate downstream must
        # treat an unusable AI step as maximally uncertain, and `>= 0.8` on a
        # None would otherwise be a silent False that reads as "the model was
        # unsure" instead of "the model never answered".
        return {
            "result": {},
            "text": "",
            "status": status,
            "error": message[:500],
            "confidence": 0.0,
            "reasoning": None,
            "detected_language": None,
            "model_used": cfg.model,
            "attempts": attempts,
        }


def _detected_language(parsed: dict[str, Any]) -> str | None:
    """Surface a language field from the author's schema as a runtime field.

    Language detection is so commonly part of the schema that promoting it keeps
    routing rules readable (`outputs.x.detected_language` works whatever the
    author named the field).
    """
    for key in ("language", "detected_language", "source_language"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _looks_like_refusal(error: Exception) -> bool:
    """Distinguish a model declining the task from a malformed response.

    Providers signal refusals differently (a `refusal` field, a stop reason, a
    content-filter error), and the gateway surfaces them as exceptions. The text
    check is a heuristic — but the alternative is reporting a refusal as
    "invalid output", which sends an author to debug their schema when the
    model simply would not answer.
    """
    text = str(error).lower()
    return any(
        marker in text
        for marker in ("refus", "content filter", "content_filter", "safety", "cannot assist")
    )
