"""
GraphNormalizer — turns source text into VALIDATED typed ProposalGraph objects.

This is the keystone node. Section drafters invent facts because they read free
text; this node converts the concept note (and call facts) into typed objects —
Objective, WorkPackage, Partner, Claim, CallRequirement — that live in
proposal_graph. Downstream, drafters read the graph, the ConsistencyChecker
gates on it, and the EvidenceAgent enriches its Claims.

Why a dedicated node (not a TransformAgent + separate ingest step)
------------------------------------------------------------------
The responsibility "LLM extracts -> validate -> write graph" belongs in one
place. The LLM proposes structure; pydantic validation is the gate. A malformed
extraction raises here and is caught as a node error — it never becomes corrupt
graph state. That is the whole point: the graph only ever holds well-formed
objects.

Trust boundary
--------------
The LLM's job is extraction/structuring of text the user supplied. It is NOT
asked to invent partners, numbers, or objectives — the prompt forbids that and
routes anything absent to open_questions. Values that cannot be validated are
dropped with a recorded warning rather than silently coerced.
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
    ComplianceObject,
    Impact,
    Innovation,
    KPI,
    Objective,
    OpenQuestion,
    Outcome,
    Partner,
    Result,
    Risk,
    Status,
    Task,
    WorkPackage,
)
from app.proposal_graph.state import proposal_graph_state_update


class GraphNormalizerOutput(BaseModel):
    """What GraphNormalizer.run() writes back. The compiler validates the
    node's output against this (output_schema is a ClassVar[Type[BaseModel]])."""
    counts: dict = Field(default_factory=dict)
    warnings: list = Field(default_factory=list)
    report: str = ""


class GraphNormalizerInput(BaseModel):
    pass


class GraphNormalizerConfig(BaseModel):
    """Node config — base.__init__ does config_schema(**raw_config)."""
    model: str = "claude-sonnet-4-5"
    max_tokens: int = Field(default=16384, ge=1024)
    # These optional fields let a zero-token WorkflowFileLoader supply the
    # extracted document text. Workflows that still use plain text inputs keep
    # working through the fallback in run().
    concept_note: str | None = None
    call_facts: str | None = None


class GraphExtraction(BaseModel):
    """Provider-enforced extraction envelope.

    Using the typed graph models here makes malformed or truncated free-text
    JSON impossible to cross the LLM boundary. The gateway asks the provider
    for this schema directly and returns a validated instance.
    """

    call_requirements: list[CallRequirement] = Field(default_factory=list)
    objectives: list[Objective] = Field(default_factory=list)
    innovations: list[Innovation] = Field(default_factory=list)
    results: list[Result] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    impacts: list[Impact] = Field(default_factory=list)
    work_packages: list[WorkPackage] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    partners: list[Partner] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    compliance: list[ComplianceObject] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

# Extraction contract handed to the LLM. Kept explicit so the JSON shape the
# model must emit is unambiguous and maps 1:1 onto the pydantic models.
_EXTRACTION_INSTRUCTIONS = """\
You convert a Horizon Europe concept note + call facts into STRUCTURED JSON.
Extract ONLY what is present in the text. Do NOT invent partners, numbers,
objectives, or KPIs. Anything an evaluator needs that is absent goes into
"open_questions", not into a fabricated object.

Return a single JSON object with these keys (all arrays; empty if none found):

"call_requirements": [{"id":"CR-EO1","text":"...","kind":"expected_outcome|scope|hard_eligibility|must_address|optional"}]
"objectives":        [{"id":"OBJ-GEN|OBJ-SO1...","text":"...","is_general":true|false,"measurable_ambition":"...|null","work_package_ids":["WP-3"]}]
"innovations":       [{"id":"INNO-1","name":"...","existing_approach":"...|null","limitation":"...|null","proposed_advance":"...|null","degree":"...|null","demonstration":"...|null","evidence_claim_ids":["CL-1"]}]
"results":           [{"id":"RES-1","name":"...","description":"...|null","from_objective_ids":["OBJ-SO1"]}]
"outcomes":          [{"id":"OUT-1","text":"...","call_requirement_id":"CR-EO1|null","from_result_ids":["RES-1"],"adoption_mechanism":"...|null"}]
"impacts":           [{"id":"IMP-1","text":"...","horizon":"short|medium|long","from_outcome_ids":["OUT-1"]}]
"work_packages":     [{"id":"WP-1","number":1,"title":"...","start_month":1|null,"end_month":18|null,"lead_partner_id":"PRT-HAW|null","partner_ids":["PRT-PI"],"objective_ids":["OBJ-SO1"],"task_ids":["TSK-1.1"]}]
"tasks":             [{"id":"TSK-1.1","work_package_id":"WP-1","title":"...","lead_partner_id":"PRT-HAW|null","output":"...|null"}]
"partners":          [{"id":"PRT-HAW","acronym":"HAW","legal_name":"...|null","country":"...|null","role":"...|null","is_end_user":true|false,"person_months":12.5|null}]
"kpis":              [{"id":"KPI-1","name":"...","definition":"...|null","baseline":"...|null","target":"...|null","unit":"...|null","measurement_source":"...|null","owner_partner_id":"PRT-HAW|null","target_date":"M36|null","linked_outcome_id":"OUT-1|null"}]
"risks":             [{"id":"RSK-1","description":"...","work_package_id":"WP-1|null","likelihood":"low|medium|high|null","impact":"...|null","mitigation":"...|null","owner_partner_id":"PRT-HAW|null"}]
"compliance":        [{"id":"CMP-GENDER","dimension":"gender|ssh|open_science|ethics|dnsh","status":"ADDRESSED|PARTIAL|MISSING","detail":{},"gaps":["..."]}]
"claims":            [{"id":"CL-1","text":"the specific state-of-the-art / problem / impact assertion","claim_type":"state_of_art|problem|impact|method","proposal_section":"1.2|null"}]
"open_questions":    [{"id":"OQ-1","text":"what is missing (e.g. partner legal names, KPI targets, 5th pilot region)","blocks_submission":true|false}]

Rules:
- CLAIMS ARE THE PRIORITY. A "claim" is ANY declarative assertion in the input
  that a proposal would need to defend or cite: a statement about the current
  state of the art, a limitation or gap, a problem, a method that has been
  applied, or an expected impact. If the input contains such assertions, you
  MUST extract them as claims — even if there are no objectives, work packages,
  or partners present. Do NOT route a genuine assertion into open_questions;
  open_questions are for things that are MISSING, not for statements that are
  present. Aim to extract every distinct assertion as its own claim.
  Examples of text that ARE claims:
    "Residues remain poorly integrated into value chains, limiting efficiency"
      -> claim_type "problem"
    "MILP has been applied to land-use allocation"
      -> claim_type "method" (or "state_of_art")
    "Valorisation introduces food-safety risks rarely assessed systematically"
      -> claim_type "problem"
- Partner ids are PRT-<ACRONYM> using the acronyms exactly as written (HAW, PI,
  AUTH, UNIMAR, CSIC, ITC, EKE, KKI, TalTech, TICASS, TUHH, GGP, AGFT, AVIPE,
  FSH, Mekreo, ...). Leave legal_name and country null — those are open_questions.
- WorkPackage lead_partner_id / partner_ids must be PRT- ids that also appear in
  "partners". If a WP lead is unclear in the text, set lead_partner_id null and
  add an open_question.
- Extract the full delivery chain when it is present:
  objective -> result -> outcome -> impact, and objective -> work package ->
  task. Preserve only explicit IDs or create stable sequential IDs when the
  source names an object without giving an ID.
- An expected-outcome CallRequirement may be linked from an Outcome only when
  the source makes that contribution credible. Do not guess the mapping.
- KPI baseline, target, unit, owner and target date are separate fields. Never
  turn an aspirational statement into a quantified target.
- Extract one ComplianceObject per dimension that the source actually
  addresses. Use PARTIAL/MISSING plus actionable gaps when the supplied text is
  incomplete; do not label generic assurances as ADDRESSED.
- Objectives must list the work_package_ids that deliver them if the text makes
  that mapping; if not, leave work_package_ids empty (the checker will flag it —
  do NOT guess a mapping).
- open_questions are ONLY for information an evaluator needs that is genuinely
  absent (missing KPIs, undefined partners, an unnamed pilot region). Never put
  a present assertion here — that belongs in claims.
- Output ONLY the JSON object. No prose, no markdown fences.

CALL FACTS:
<<CALL_FACTS>>

CONCEPT NOTE:
<<CONCEPT_NOTE>>
"""

_MODEL_BY_KEY = {
    "call_requirements": CallRequirement,
    "objectives": Objective,
    "innovations": Innovation,
    "results": Result,
    "outcomes": Outcome,
    "impacts": Impact,
    "work_packages": WorkPackage,
    "tasks": Task,
    "partners": Partner,
    "kpis": KPI,
    "risks": Risk,
    "compliance": ComplianceObject,
    "claims": Claim,
    "open_questions": OpenQuestion,
}
# graph collection attribute name for each extraction key
_COLLECTION_BY_KEY = {
    "call_requirements": "call_requirements",
    "objectives": "objectives",
    "innovations": "innovations",
    "results": "results",
    "outcomes": "outcomes",
    "impacts": "impacts",
    "work_packages": "work_packages",
    "tasks": "tasks",
    "partners": "partners",
    "kpis": "kpis",
    "risks": "risks",
    "compliance": "compliance",
    "claims": "claims",
    "open_questions": "open_questions",
}


@NodeRegistry.register
class GraphNormalizer(NodeType):
    """LLM extracts structure from source text; pydantic validates it; valid
    objects are written to proposal_graph. Invalid items are dropped with a
    warning rather than corrupting the graph."""

    type_name = "GraphNormalizer"

    # ClassVars — base.__init__ does config_schema(**raw_config); compiler does
    # output_schema(**run_output). Both must be pydantic model classes.
    input_schema = GraphNormalizerInput
    config_schema = GraphNormalizerConfig
    output_schema = GraphNormalizerOutput

    async def run(self, state: dict, config: dict) -> dict:
        inputs = state.get("inputs", {})
        # Accept either the proposal-style inputs (concept_note + call_facts) or
        # zero-token file-loader output supplied through config. Plain-text and
        # smoke-test workflows remain backward compatible.
        concept_note = (
            config.get("concept_note")
            or inputs.get("concept_note")
            or inputs.get("topic_text")
            or inputs.get("text")
            or ""
        )
        call_facts = config.get("call_facts") or inputs.get("call_facts") or ""

        prompt = (
            _EXTRACTION_INSTRUCTIONS
            .replace("<<CALL_FACTS>>", call_facts)
            .replace("<<CONCEPT_NOTE>>", concept_note)
        )

        llm = self.services.get("llm")
        if llm is None:
            raise RuntimeError(
                "GraphNormalizer requires an 'llm' service. It was not present in "
                "node services — ensure the workflow is run with the app's services "
                "dict (llm gateway), not an empty/None services."
            )
        extraction = await llm.complete_structured(
            system=(
                "You are a Horizon Europe proposal analyst that extracts "
                "structured data. Extract only information supported by the "
                "provided text and populate the requested schema."
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

        for key, model_cls in _MODEL_BY_KEY.items():
            items = extracted.get(key, []) or []
            collection: dict[str, Any] = {}
            for item in items:
                if not isinstance(item, dict):
                    warnings.append(f"{key}: skipped non-object entry")
                    continue
                try:
                    obj = model_cls(**item)
                except ValidationError as ve:
                    warnings.append(f"{key}: dropped invalid item "
                                    f"{item.get('id', '?')} ({ve.error_count()} errors)")
                    continue
                collection[obj.id] = obj
            setattr(graph_delta, _COLLECTION_BY_KEY[key], collection)
            counts[key] = len(collection)

        report = ("GraphNormalizer extracted: "
                  + ", ".join(f"{k}={v}" for k, v in counts.items())
                  + (f"; {len(warnings)} warnings" if warnings else ""))

        return {
            # node's own output — validated against GraphNormalizerOutput
            "counts": counts,
            "warnings": warnings,
            "report": report,
            # state-channel write — passed straight through by the compiler to
            # the proposal_graph reducer (NOT folded into node_outputs)
            "__state__": proposal_graph_state_update(graph_delta),
        }

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Parse the LLM's JSON and fail loudly when extraction is malformed."""
        s = raw.strip()
        if s.startswith("```"):
            # strip a ```json ... ``` fence if the model added one
            s = s.split("```", 2)
            s = s[1] if len(s) >= 2 else raw
            if s.lstrip().lower().startswith("json"):
                s = s.lstrip()[4:]
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            # last resort: find the outermost {...}
            try:
                start, end = s.index("{"), s.rindex("}")
                return json.loads(s[start:end + 1])
            except (ValueError, json.JSONDecodeError):
                raise ValueError(
                    "GraphNormalizer received invalid JSON from the model"
                )
