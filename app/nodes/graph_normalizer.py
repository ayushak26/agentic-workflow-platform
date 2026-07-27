"""
GraphNormalizer — turns source text into VALIDATED typed ProposalGraph objects.

The LLM extracts structured information from the concept note. Pydantic
validation prevents malformed objects from entering the proposal graph.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.proposal_graph.graph import ProposalGraph
from app.proposal_graph.models import (
    CallRequirement,
    Claim,
    Objective,
    OpenQuestion,
    Partner,
    Status,
    WorkPackage,
)
from app.proposal_graph.state import proposal_graph_state_update


class GraphNormalizerOutput(BaseModel):
    """Output returned by GraphNormalizer."""

    counts: dict = Field(default_factory=dict)
    warnings: list = Field(default_factory=list)
    report: str = ""


class GraphNormalizerConfig(BaseModel):
    """Configuration accepted by GraphNormalizer."""

    model: str | None = None
    max_tokens: int = Field(default=16384, ge=1024)


class GraphExtraction(BaseModel):
    """Schema-enforced response returned by the model."""

    call_requirements: list[CallRequirement] = Field(default_factory=list)
    objectives: list[Objective] = Field(default_factory=list)
    work_packages: list[WorkPackage] = Field(default_factory=list)
    partners: list[Partner] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


_EXTRACTION_INSTRUCTIONS = """\
You convert a Horizon Europe concept note and call facts into structured data.

Extract ONLY information present in the supplied text. Do not invent partners,
numbers, objectives, work packages, KPIs, results, methods, or impacts.

Anything an evaluator needs that is absent must be placed in open_questions.

Required output fields:

call_requirements:
- id
- text
- kind

objectives:
- id
- text
- is_general
- measurable_ambition
- work_package_ids

work_packages:
- id
- number
- title
- start_month
- end_month
- lead_partner_id
- partner_ids
- objective_ids

partners:
- id
- acronym
- legal_name
- country
- role
- is_end_user

claims:
- id
- text
- claim_type
- proposal_section

open_questions:
- id
- text
- blocks_submission

Rules:

1. Claims are a priority.

A claim is any assertion that may require evidence or a citation, including:

- state-of-the-art statements;
- existing scientific or technical limitations;
- market or policy problems;
- methodological statements;
- expected impact statements.

Extract each distinct assertion as a separate claim.

Examples:

"Residues remain poorly integrated into value chains."

This is a problem claim.

"MILP has been applied to land-use allocation."

This is a method or state-of-the-art claim.

"Valorisation introduces food-safety risks that are rarely assessed."

This is a problem claim.

2. Open questions are only for missing information.

Do not place an existing assertion in open_questions. Existing assertions
belong in claims.

3. Partner identifiers must follow this pattern:

PRT-<ACRONYM>

Use partner acronyms exactly as written in the input.

If the legal name or country is not provided, leave it empty instead of
inventing it.

4. Work-package partner references must use partner identifiers that also
appear in the partners collection.

If the work-package leader is unknown, leave lead_partner_id empty and create
an open question.

5. Objectives should reference the work packages that deliver them only when
the mapping is supported by the input.

If the mapping is not available, leave work_package_ids empty.

6. Never guess missing values.

CALL FACTS:
<<CALL_FACTS>>

CONCEPT NOTE:
<<CONCEPT_NOTE>>
"""


_MODEL_BY_KEY = {
    "call_requirements": CallRequirement,
    "objectives": Objective,
    "work_packages": WorkPackage,
    "partners": Partner,
    "claims": Claim,
    "open_questions": OpenQuestion,
}


_COLLECTION_BY_KEY = {
    "call_requirements": "call_requirements",
    "objectives": "objectives",
    "work_packages": "work_packages",
    "partners": "partners",
    "claims": "claims",
    "open_questions": "open_questions",
}


@NodeRegistry.register
class GraphNormalizer(NodeType):
    """Extract and validate structured proposal information."""

    type_name = "GraphNormalizer"

    config_schema = GraphNormalizerConfig
    output_schema = GraphNormalizerOutput

    async def run(self, state: dict, config: dict) -> dict:
        inputs = state.get("inputs", {})

        concept_note = (
            inputs.get("concept_note")
            or inputs.get("topic_text")
            or inputs.get("text")
            or ""
        )

        call_facts = inputs.get("call_facts", "")

        prompt = (
            _EXTRACTION_INSTRUCTIONS
            .replace("<<CALL_FACTS>>", call_facts)
            .replace("<<CONCEPT_NOTE>>", concept_note)
        )

        llm = self.services.get("llm")

        if llm is None:
            raise RuntimeError(
                "GraphNormalizer requires an 'llm' service. "
                "Ensure the workflow is executed with the application's "
                "LLM gateway in the services dictionary."
            )

        extraction = await llm.complete_structured(
            system=(
                "You are a Horizon Europe proposal analyst. "
                "Extract only information supported by the supplied text "
                "and populate the requested response schema."
            ),
            user=prompt,
            model=config.get("model"),
            response_model=GraphExtraction,
            temperature=0.0,
            max_tokens=config.get("max_tokens", 16384),
        )

        extracted = extraction.model_dump(mode="python")

        graph_delta = ProposalGraph()
        counts: dict[str, int] = {}
        warnings: list[str] = []

        for key, model_class in _MODEL_BY_KEY.items():
            items = extracted.get(key, []) or []
            collection: dict[str, Any] = {}

            for item in items:
                if not isinstance(item, dict):
                    warnings.append(
                        f"{key}: skipped entry because it was not an object"
                    )
                    continue

                try:
                    graph_object = model_class(**item)
                except ValidationError as validation_error:
                    item_id = item.get("id", "?")
                    warnings.append(
                        f"{key}: dropped invalid item {item_id} "
                        f"({validation_error.error_count()} validation errors)"
                    )
                    continue

                collection[graph_object.id] = graph_object

            collection_name = _COLLECTION_BY_KEY[key]
            setattr(graph_delta, collection_name, collection)
            counts[key] = len(collection)

        report = (
            "GraphNormalizer extracted: "
            + ", ".join(f"{key}={value}" for key, value in counts.items())
        )

        if warnings:
            report += f"; {len(warnings)} warnings"

        return {
            "counts": counts,
            "warnings": warnings,
            "report": report,
            "__state__": proposal_graph_state_update(graph_delta),
        }

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Compatibility parser for older callers.

        The normal execution path now uses complete_structured() and does not
        depend on free-text JSON parsing.
        """

        text = raw.strip()

        if text.startswith("```"):
            fenced_parts = text.split("```", 2)
            text = fenced_parts[1] if len(fenced_parts) >= 2 else raw

            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]

        try:
            parsed = json.loads(text)

            if not isinstance(parsed, dict):
                raise ValueError(
                    "GraphNormalizer expected a JSON object from the model"
                )

            return parsed

        except json.JSONDecodeError:
            try:
                object_start = text.index("{")
                object_end = text.rindex("}")

                parsed = json.loads(text[object_start : object_end + 1])

                if not isinstance(parsed, dict):
                    raise ValueError(
                        "GraphNormalizer expected a JSON object from the model"
                    )

                return parsed

            except (ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    "GraphNormalizer received invalid JSON from the model"
                ) from error