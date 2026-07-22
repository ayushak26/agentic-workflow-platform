"""
Proposal Knowledge Graph — typed objects.

This is the single source of truth a proposal is written FROM. Section drafters
read these objects; they do not invent objectives, partners, numbers or KPIs.
Every object carries a stable string id so relationships are by-reference, and a
`status` where a human decision or a missing fact needs to be tracked
(fail-loud: an empty required field is visible, not silently absent).

Design notes
------------
- Pure pydantic v2 models, no runtime deps beyond pydantic. Safe to import
  anywhere (nodes, checker, tests).
- Every model is additive to the existing WorkflowState. Nothing here replaces
  a field an existing node or test relies on.
- IDs are conventionally prefixed (CR- CL- OBJ- INNO- RES- OUT- IMP- WP- TSK-
  PRT- KPI- RSK- ETH- and the four compliance singletons) so a reference like
  KPI.owner_partner_id="PRT-HAW" is self-describing.
- `Status` is the fail-loud primitive. A drafter/normaliser sets MISSING with a
  concrete `gaps` request rather than writing a reassuring sentence.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    ADDRESSED = "ADDRESSED"   # a specific, grounded fact exists
    PARTIAL = "PARTIAL"       # present but incomplete / weak
    MISSING = "MISSING"       # required fact absent — must be supplied by a human


class Authority(str, Enum):
    """Provenance strength for evidence — used by the evidence layer later."""
    PEER_REVIEWED = "peer_reviewed"
    OFFICIAL_EU = "official_eu"          # CORDIS, EUR-Lex, WP text, EC guidance
    PREPRINT = "preprint"
    GREY = "grey"                        # reports, white papers
    PARTNER_CLAIM = "partner_claim"      # asserted by a consortium partner
    UNVERIFIED = "unverified"


# ---------------------------------------------------------------------------
# 1. Call requirement — an atomic thing the call demands the proposal cover.
# ---------------------------------------------------------------------------
class CallRequirement(BaseModel):
    id: str                                  # "CR-EO1", "CR-SCOPE-MAA"
    text: str
    kind: str = "must_address"               # hard_eligibility|expected_outcome|
                                             # scope|must_address|optional
    addressed_by_section: Optional[str] = None   # e.g. "1.2", "2.1"
    coverage: Status = Status.MISSING


# ---------------------------------------------------------------------------
# 2. Evidence source + 3. Claim (claim -> evidence -> locator -> verification)
# ---------------------------------------------------------------------------
class EvidenceSource(BaseModel):
    id: str                                  # "SRC-038"
    citation: str                            # human-readable ref
    identifier: Optional[str] = None         # DOI / arXiv / CORDIS id / URL
    authority: Authority = Authority.UNVERIFIED
    retrieved_at: Optional[str] = None


class Claim(BaseModel):
    id: str                                  # "CL-014"
    text: str
    claim_type: str = "state_of_art"         # problem|state_of_art|impact|method
    proposal_section: Optional[str] = None
    evidence_source_ids: list[str] = Field(default_factory=list)
    locator: Optional[str] = None            # "p.14, sec 3.2"
    verification: Status = Status.MISSING


# ---------------------------------------------------------------------------
# 4. Objective  5. Innovation  6. Result  7. Outcome  8. Impact
#    (the Excellence + Impact backbone)
# ---------------------------------------------------------------------------
class Objective(BaseModel):
    id: str                                  # "OBJ-GEN", "OBJ-SO1"
    text: str
    is_general: bool = False
    measurable_ambition: Optional[str] = None
    work_package_ids: list[str] = Field(default_factory=list)   # must be non-empty
    status: Status = Status.PARTIAL


class Innovation(BaseModel):
    id: str                                  # "INNO-IARGF", "INNO-RBOS"
    name: str
    existing_approach: Optional[str] = None
    limitation: Optional[str] = None
    proposed_advance: Optional[str] = None
    degree: Optional[str] = None             # incremental|substantial|breakthrough
    demonstration: Optional[str] = None      # how the advance is shown
    evidence_claim_ids: list[str] = Field(default_factory=list)
    status: Status = Status.PARTIAL


class Result(BaseModel):
    id: str                                  # "RES-IARGF"
    name: str
    description: Optional[str] = None
    from_objective_ids: list[str] = Field(default_factory=list)


class Outcome(BaseModel):
    id: str                                  # "OUT-1"
    text: str
    call_requirement_id: Optional[str] = None    # ties to an expected outcome
    from_result_ids: list[str] = Field(default_factory=list)
    adoption_mechanism: Optional[str] = None


class Impact(BaseModel):
    id: str                                  # "IMP-1"
    text: str
    horizon: str = "long"                    # short|medium|long
    from_outcome_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. Work package  10. Task  11. Partner  (the Implementation backbone)
# ---------------------------------------------------------------------------
class Task(BaseModel):
    id: str                                  # "TSK-3.1"
    work_package_id: str                     # "WP-3"
    title: str
    lead_partner_id: Optional[str] = None
    output: Optional[str] = None             # verifiable output / deliverable ref


class WorkPackage(BaseModel):
    id: str                                  # "WP-3"
    number: int
    title: str
    start_month: Optional[int] = None
    end_month: Optional[int] = None
    lead_partner_id: Optional[str] = None    # must be set — checker enforces
    partner_ids: list[str] = Field(default_factory=list)
    objective_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    status: Status = Status.PARTIAL


class Partner(BaseModel):
    id: str                                  # "PRT-HAW"
    acronym: str
    legal_name: Optional[str] = None         # MISSING until consortium supplies
    country: Optional[str] = None
    role: Optional[str] = None
    is_end_user: bool = False                # multi-actor coverage tracking
    person_months: Optional[float] = None    # per-partner effort (nullable)
    status: Status = Status.PARTIAL


# ---------------------------------------------------------------------------
# 12. KPI  13. Risk
# ---------------------------------------------------------------------------
class KPI(BaseModel):
    id: str                                  # "KPI-1"
    name: str
    definition: Optional[str] = None
    baseline: Optional[str] = None           # nullable — checker flags if empty
    target: Optional[str] = None
    unit: Optional[str] = None
    measurement_source: Optional[str] = None
    owner_partner_id: Optional[str] = None   # checker enforces non-null
    target_date: Optional[str] = None
    linked_outcome_id: Optional[str] = None
    status: Status = Status.MISSING


class Risk(BaseModel):
    id: str                                  # "RSK-1"
    description: str
    work_package_id: Optional[str] = None
    likelihood: Optional[str] = None         # low|medium|high
    impact: Optional[str] = None
    mitigation: Optional[str] = None
    owner_partner_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 14. Compliance objects (gender / SSH / open science / ethics / DNSH)
#     — the four you asked about, now first-class graph citizens.
# ---------------------------------------------------------------------------
class ComplianceObject(BaseModel):
    id: str                                  # "CMP-GENDER" ...
    dimension: str                           # gender|ssh|open_science|ethics|dnsh
    status: Status = Status.MISSING
    detail: dict = Field(default_factory=dict)   # dimension-specific fields
    gaps: list[str] = Field(default_factory=list)  # actionable human requests


# ---------------------------------------------------------------------------
# 15. Open question — anything the system knows it does not yet know.
# ---------------------------------------------------------------------------
class OpenQuestion(BaseModel):
    id: str                                  # "OQ-1"
    text: str
    blocks_submission: bool = False
    owner: Optional[str] = None              # who must answer it