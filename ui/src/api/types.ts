// Mirrors app/runtime/schema.py and app/nodes/registry.py manifest output.
/** How a node reaches its result. Drives the canvas badge that makes the
 *  automation boundary visible: a model decided this, code decided this,
 *  something outside the platform changed, a person decided. */
export type ExecutionKind =
  | 'ai'
  | 'deterministic'
  | 'external'
  | 'human'
  | 'input'
  | 'output';

/** A configuration starting point offered in the inspector. Presets are never
 *  separate node types — picking one only writes config. */
export type NodePreset = {
  id: string;
  label: string;
  summary?: string;
  task?: string;
  instruction?: string;
  include_confidence?: boolean;
  config?: Record<string, unknown>;
  rules?: unknown[];
  external_action?: boolean;
};

export type NodeAbout = {
  what?: string;
  why?: string;
  receives?: string;
  produces?: string;
  uses_ai?: boolean;
  external_action?: boolean;
  safety?: string;
  presets?: NodePreset[];
  operators?: Record<string, string[]>;
};

export type NodeTypeManifest = {
  type_name: string;
  description: string;
  category: string;
  icon: string;
  /** 'core' = the small reusable vocabulary a new workflow starts from;
   *  'specialized' = an existing domain capability, still available. */
  family: 'core' | 'specialized';
  execution_kind: ExecutionKind;
  uses_ai: boolean;
  external_action: boolean;
  about: NodeAbout;
  presets: NodePreset[];
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  config_schema: Record<string, unknown>;
};

// ---- Visual structured-output schema (mirrors app/runtime/field_schema.py) ----

export type FieldKind =
  | 'string'
  | 'text'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'enum'
  | 'object'
  | 'list'
  | 'date';

export type FieldSpec = {
  name: string;
  type: FieldKind;
  description?: string;
  required?: boolean;
  nullable?: boolean;
  enum_values?: string[];
  fields?: FieldSpec[];
  item_type?: FieldKind | null;
  item_enum_values?: string[];
  minimum?: number | null;
  maximum?: number | null;
};

export type SchemaPreview = {
  json_schema: Record<string, unknown>;
  contract: string;
  paths: Array<{
    path: string;
    type: string;
    description: string;
    required: boolean;
    nullable: boolean;
    enum_values: string[];
    may_be_unavailable: boolean;
  }>;
};

// ---- Rules (mirrors app/runtime/rules.py) ----

export type RuleOperator = string;

export type RuleCondition = {
  field: string;
  operator: RuleOperator;
  value?: unknown;
};

export type RuleConditionGroup = {
  operator: 'and' | 'or' | 'not';
  conditions: Array<RuleCondition | RuleConditionGroup>;
};

export type RuleAction = {
  field: string;
  operation?: 'set' | 'append' | 'increase' | 'decrease';
  value?: unknown;
};

export type BusinessRule = {
  name: string;
  description?: string;
  when?: RuleConditionGroup | null;
  then: RuleAction[];
  default?: boolean;
  stop_on_match?: boolean;
};

export type OperatorCatalog = {
  by_type: Record<string, string[]>;
  labels: Record<string, string>;
  /** 'none' = no value input, 'one' = a single value, 'many' = a value list. */
  arity: Record<string, 'none' | 'one' | 'many'>;
};

// ---- Output contract / mapping ----

export type ContractField = {
  path: string;
  /** The template reference to write into config — the author never types it. */
  reference: string;
  type: string;
  description: string;
  required: boolean;
  may_be_unavailable: boolean;
  enum_values: string[];
  item_type: string | null;
  operators: string[];
};

export type ContractNode = {
  node_id: string;
  type_name: string;
  label: string;
  execution_kind: ExecutionKind;
  typed: boolean;
  fields: ContractField[];
};

export type OutputContract = {
  nodes: ContractNode[];
  inputs: Array<{
    name: string;
    reference: string;
    type: string;
    description: string;
    required: boolean;
  }>;
  variables: Array<{ name: string; reference: string; type: string }>;
};

// ---- Node test / simulation ----

export type StepExplanation = {
  kind: ExecutionKind;
  decided_by?: string;
  status?: string;
  confidence?: number | null;
  detected_language?: string | null;
  model_used?: string | null;
  reasoning?: string | null;
  route?: string;
  route_value?: unknown;
  used_fallback?: boolean;
  matched_rules?: string[];
  decisions?: Record<string, unknown>;
  decision?: string;
  operation?: string;
  deduplicated?: boolean;
  defaulted?: string[];
  summary: string[];
  rules?: unknown[];
  conditions?: unknown[];
};

export type NodeTestResult = {
  status: 'completed' | 'failed';
  node_id: string;
  type_name: string;
  output?: Record<string, unknown>;
  error?: string;
  error_type?: string;
  duration_s: number;
  resolved_config?: Record<string, unknown>;
  explanation?: StepExplanation;
};

export type SimulationStep = {
  node_id: string;
  label: string;
  type_name: string;
  execution_kind: ExecutionKind;
  stubbed: boolean;
  status?: 'waiting';
  duration_s: number | null;
  output: Record<string, unknown> | null;
  review?: Record<string, unknown>;
  explanation: StepExplanation;
};

export type SimulationResult = {
  simulation_id: string;
  status: string;
  duration_s: number;
  steps: SimulationStep[];
  path: string[];
  output?: unknown;
  interrupt?: unknown;
  waiting_for?: string[];
  stubbed?: string[];
  error?: string;
  error_type?: string;
};

// ---- MCP integration ----

/** How a tool affects the outside world. Rendered on the canvas so the
 *  automation boundary is visible before anything runs. */
export type MCPOperationClass = 'read' | 'write' | 'destructive' | 'unknown';

export type MCPServerInfo = {
  id: string;
  display_name: string;
  description: string;
  transport: string;
  environment_label: string;
  /** True when the connection is backed by fixtures rather than a live system.
   *  Shown prominently — a "Connected" badge over a mock is how a demo becomes
   *  a lie. */
  is_mock: boolean;
  write_policy: 'read_only' | 'require_approval' | 'allow';
  tool_allowlist: string[];
  timeout_seconds: number;
  running: boolean;
  /** Which credentials the connection expects, and whether each is set. Never
   *  a credential value. */
  credentials: Array<{
    variable: string;
    reference: string;
    configured: boolean;
  }>;
  status: {
    healthy: boolean | null;
    tool_count: number;
    error: string | null;
    checked_at: string | null;
  };
};

export type MCPToolField = {
  path: string;
  type: string;
  description: string;
  required: boolean;
  enum_values: string[];
};

export type MCPToolInfo = {
  server_id: string;
  server_label: string;
  name: string;
  title: string;
  description: string;
  operation: MCPOperationClass;
  external_action: boolean;
  requires_approval: boolean;
  system: string;
  typical_uses: string[];
  mode: string;
  /** JSON Schema the Builder renders as a form — never hardcoded in React. */
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  /** Typed result paths for the mapping picker. */
  output_fields: MCPToolField[];
};

export type MCPToolTestResult = {
  status: 'completed' | 'failed';
  server_id: string;
  tool: string;
  operation?: MCPOperationClass;
  mode?: string;
  duration_s?: number;
  is_structured?: boolean;
  data?: unknown;
  text?: unknown;
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    suggested_action?: string;
  };
};

export type EmailConnectionInfo = {
  id: string;
  provider: string;
  display_name: string;
  address: string;
  allow_send: boolean;
};

export type LLMModelInfo = {
  name: string;
  display_name: string;
  provider: string;
  local: boolean;
  automatic?: boolean;
  enabled: boolean;
  configured: boolean;
  tool_calling: boolean;
  structured_output: boolean;
  reasoning_efforts: string[];
  platform_modalities: string[];
  upstream_url?: string | null;
  description?: string | null;
};

// Mirrors app/runtime/schema.py's LibraryMetadataSpec + friends, as returned
// (declared or honestly-derived) by app.workflow.library_metadata.
export type LibraryVisibilityStatus = 'approved' | 'draft' | 'in_review' | 'deprecated' | 'archived';

export type LibraryDurationRange = {
  minimum_minutes: number | null;
  maximum_minutes: number | null;
};

export type LibraryHumanReviews = {
  count: number;
  labels: string[];
};

export type LibraryEvidencePolicy = {
  drafting_requires_verified_evidence: boolean | null;
  deep_research_is_context_only: boolean | null;
};

export type LibraryMetadata = {
  title: string;
  summary: string;
  purpose: string[];
  suitable_for: string[];
  not_suitable_for: string[];
  outputs: string[];
  input_types: string[];
  typical_duration: LibraryDurationRange | null;
  human_reviews: LibraryHumanReviews;
  evidence_policy: LibraryEvidencePolicy | null;
  visibility_status: LibraryVisibilityStatus;
  owner_team: string | null;
  // False for every pre-existing workflow: everything above except
  // `human_reviews.count` (derived from the graph) and `outputs` (a naming
  // heuristic) is an honest fallback, not authored fact.
  declared: boolean;
};

export type ReadinessLevel = 'ready' | 'ready_with_warnings' | 'blocked';

export type ReadinessItem = {
  severity: 'error' | 'warning';
  code: string;
  message: string;
  suggestion: string | null;
};

export type ReadinessSummary = {
  level: ReadinessLevel;
  items: ReadinessItem[];
};

export type WorkflowStats = {
  sample_size: number;
  completed_runs: number;
  failed_runs: number;
  enough_data_for_estimates: boolean;
  success_rate: number | null;
  median_duration_s: number | null;
  most_common_failure: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  last_successful_run_at: string | null;
};

export type WorkflowSummary = {
  name: string;
  description: string;
  use_case: string;
  version: string;
  node_count: number;
  updated_at: string;
  library: LibraryMetadata | null;
  readiness: ReadinessSummary;
};

export type WorkflowDetail = {
  name: string;
  description: string;
  use_case: string;
  version: string;
  node_count: number;
  updated_at: string;
  library: LibraryMetadata;
  readiness: ReadinessSummary;
};

export type PreflightSeverity = 'error' | 'warning';

export type PreflightIssue = {
  code: string;
  severity: PreflightSeverity;
  message: string;
  path?: string | null;
  node_id?: string | null;
  suggestion?: string | null;
};

export type PreflightCheck = {
  name: string;
  status: 'passed' | 'failed' | 'warning' | 'skipped';
  detail: string;
};

export type WorkflowPreflightReport = {
  valid: boolean;
  workflow_name?: string | null;
  node_count: number;
  edge_count: number;
  required_services: string[];
  checks: PreflightCheck[];
  issues: PreflightIssue[];
  tokens_spent: number;
};

export type AutofixWorkflowResult = {
  yaml: string;
  fixed: boolean;
  deterministic_fixes_applied: string[];
  llm_attempts: { success: boolean; detail: string }[];
  preflight_report: WorkflowPreflightReport;
};

// Mirrors app/workflow/builder_store.py's save_draft/read_draft document.
export type WorkflowDraft = {
  name: string;
  updated_at: string;
  sha256: string;
  base_sha256: string | null;
  yaml: string;
  canvas: {
    nodes?: Array<{ id: string; position: { x: number; y: number } }>;
    viewport?: { x: number; y: number; zoom: number };
    selected_node_id?: string | null;
  };
  current_sha256: string | null;
  differs_from_current: boolean;
};

// Mirrors app/workflow/builder_store.py's list_versions entries.
export type WorkflowVersionSummary = {
  version_id: string;
  created_at: string;
  sha256: string;
  current: boolean;
  workflow_version: string;
  node_count: number;
  description: string;
};

export type WorkflowFileReference = {
  kind: 'workflow_file';
  file_id: string;
  name: string;
  extension: string;
  category: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  minio_key: string;
  parseable_text: boolean;
};

export type WorkflowFileCapabilities = {
  categories: Record<string, string[]>;
  extensions: string[];
  extractable_extensions: string[];
  reference_only_extensions: string[];
  max_file_size_bytes: number;
  max_files_per_input: number;
};

export type HITLReviewContent = {
  text: string;
  html?: string | null;
  format?: 'text' | 'json';
  source: 'workflow' | 'editor' | 'upload';
  source_path?: string | null;
  source_document?: WorkflowFileReference | null;
};

export type ExtractedWorkflowFile = {
  file: WorkflowFileReference;
  text: string;
  total_chars: number;
  extracted_chars: number;
  truncated: boolean;
};

export type RunEvent =
  | { type: 'node_started'; run_id: string; node_id: string; ts: string; event_id?: number }
  | { type: 'node_completed'; run_id: string; node_id: string; output_preview: string; ts: string; event_id?: number }
  | { type: 'node_reused'; run_id: string; node_id: string; output_preview: string; ts: string; event_id?: number }
  | { type: 'node_paused'; run_id: string; node_id: string; context: Record<string, unknown>; ts: string; event_id?: number }
  | { type: 'run_completed'; run_id: string; ts: string; event_id?: number }
  | { type: 'run_rejected'; run_id: string; node_id?: string; error?: string; ts: string; event_id?: number }
  | { type: 'run_failed'; run_id: string; node_id?: string; error: string; ts: string; event_id?: number };

export type RunSnapshot = {
  run_id: string;
  status: 'running' | 'paused' | 'completed' | 'failed';
  node_states: Record<string, 'pending' | 'active' | 'done' | 'reused' | 'paused' | 'failed'>;
};

export type RunStatus =
  | 'running'
  | 'paused'
  | 'completed'
  | 'rejected'
  | 'failed';

export type EventType =
  | 'node_start'
  | 'node_end'
  | 'node_reused'
  | 'node_error'
  | 'hitl_approve'
  | 'hitl_reject'
  | 'hitl_edit';

export type PauseKind = 'hitl_gate' | 'user_requested';

// GET /api/runs/mine/{run_id}/pending-gate — reconstructs the same HITL gate
// a live Cockpit tab would have shown, from the durable checkpoint, so it can
// be re-displayed after a fresh page load (Run History reopened later, a
// different tab).
export type PendingGate =
  | { run_id: string; paused: false }
  | { run_id: string; paused: true; pause_kind: 'user_requested'; node_id: string | null }
  | {
      run_id: string;
      paused: true;
      pause_kind: 'hitl_gate';
      node_id: string;
      question: string;
      context: Record<string, unknown> | null;
      allowed_actions: string[];
      content: HITLReviewContent | null;
      allow_document_override: boolean;
      max_edit_chars: number;
    };

export interface RunSummary {
  run_id: string;
  session_id: string;
  workflow_name: string;
  status: RunStatus;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  node_count: number | null;
  completed_node_count: number;
  reused_node_count?: number;
  reused_nodes?: string[];
  retry_of_run_id?: string | null;
  attempt?: number;
  active_nodes: string[];
  last_completed_node?: string | null;
  failed_node?: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  // Present only while status === 'paused'. Distinguishes a cooperative
  // pause requested from run history ('user_requested', resumable with no
  // decision) from a HITL gate's own pause ('hitl_gate', resumed from the
  // review screen with an approve/reject/edit decision instead).
  pause_kind?: PauseKind;
  // Present only when this run is one stage of a pipeline (see
  // app/runtime/pipeline_executor.py's _run_stage) — stamped at run
  // creation so Run History can show/search/filter/sort by stage without a
  // separate lookup into the pipeline_runs collection.
  pipeline_run_id?: string | null;
  pipeline_name?: string | null;
  stage_id?: string | null;
  stage_index?: number | null;
  total_stages?: number | null;
}

export type NodeRunStatus =
  | 'running'
  | 'paused'
  | 'completed'
  | 'reused'
  | 'failed';

export interface ModelSelection {
  call_id: string;
  requested_model: string;
  actual_model: string;
  mode: string;
  complexity: string;
  task_kind: string;
  reason: string;
  fallback: boolean;
  cache_hit: boolean;
}
export interface NodeRun {
  node_id: string;
  type_name: string;
  status: NodeRunStatus;
  input: Record<string, unknown>;
  output: unknown;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  error: string | null;
  error_type?: string | null;
  error_traceback?: string | null;
  model_selections?: ModelSelection[];
}

export interface GenerateWorkflowAttempt {
  stage: 'static' | 'real_execution';
  success: boolean;
  detail: string;
}

export interface GenerateWorkflowResult {
  yaml: string;
  success: boolean;
  preflight_report: WorkflowPreflightReport | null;
  execution_result: { status: string; error: string | null } | null;
  execution_skipped_reason: string | null;
  attempts: GenerateWorkflowAttempt[];
}

export interface RunChatTurn {
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  ts: number;
}

export interface AuditEvent {
  run_id: string;
  session_id: string;
  node_id: string;
  event_type: EventType;
  actor: string;
  payload: Record<string, unknown>;
  ts: string;
}
export interface RunDetail extends RunSummary {
  inputs: Record<string, unknown>;
  variables?: Record<string, unknown>;
  outputs: Record<string, unknown>;
  node_runs: Record<string, NodeRun>;
  node_types?: Record<string, string>;
  workflow_yaml?: string;
  retry_available?: boolean;
  retryable_node_count?: number;
}

export type PipelineSummary = {
  name: string;
  description: string;
  version: string;
  stage_count: number;
};

export type PipelinePreflightReport = {
  valid: boolean;
  pipeline_name?: string | null;
  stage_count: number;
  checks: PreflightCheck[];
  issues: PreflightIssue[];
};

export type PipelineStageStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'rejected';

export interface PipelineStageResult {
  id: string;
  workflow: string;
  run_id: string | null;
  status: PipelineStageStatus;
  error?: string | null;
}

export type PipelineRunStatus = 'running' | 'gated' | 'completed' | 'failed' | 'abandoned';

export interface PipelineRunSummary {
  pipeline_run_id: string;
  session_id: string;
  pipeline_name: string;
  status: PipelineRunStatus;
  current_stage_index: number;
  stages: PipelineStageResult[];
  created_at: string;
  updated_at: string;
  ended_at?: string | null;
}

export interface PipelineRunDetail extends PipelineRunSummary {
  pipeline_inputs: Record<string, unknown>;
  pipeline_yaml?: string;
}

export interface PipelineStageOutcome {
  pipeline_run_id: string;
  stage_id: string;
  stage_run_id: string;
  stage_result: {
    status: string;
    run_id: string;
    error?: string;
    state?: unknown;
    output?: Record<string, unknown>;
    node_id?: string;
    reason?: string;
  };
  pipeline: PipelineRunDetail;
}

export type CoverageStatus = 'ADDRESSED' | 'PARTIAL' | 'MISSING';

export interface CallCoverageRow {
  requirement_id: string;
  kind: string;
  requirement: string;
  status: CoverageStatus;
  section?: string | null;
  mapped_object_ids: string[];
  evidence_claim_ids: string[];
  verified_claim_count: number;
  missing_items: string[];
  owner_partner_ids: string[];
  blocking: boolean;
}

export interface CallCoverageMatrix {
  rows: CallCoverageRow[];
  addressed: number;
  partial: number;
  missing: number;
  coverage_percent: number;
  blocking_requirement_ids: string[];
  submission_blocked: boolean;
}

export interface ProposalApproval {
  approval_id: string;
  proposal_id: string;
  stage: string;
  snapshot_id: string;
  snapshot_version: number;
  snapshot_sha256: string;
  status: 'pending' | 'approved' | 'rejected' | 'changes_requested';
  selected_concept_id?: string | null;
  coverage: CallCoverageMatrix;
  requested_by: string;
  requested_at: string;
  decided_by?: string | null;
  decided_at?: string | null;
  comment?: string | null;
}

export interface ConceptAlternative {
  id: string;
  posture: 'conservative' | 'balanced' | 'ambitious';
  title: string;
  summary: string;
  scientific_advance: string;
  scope: string;
  call_requirement_ids: string[];
  objective_ids: string[];
  evidence_claim_ids: string[];
  required_capabilities: string[];
  assumptions: string[];
  key_risks: string[];
  evidence_weighted_score: number;
}

export interface ProposalSourceVersion {
  version_id: string;
  source_id: string;
  version: number;
  title: string;
  content_sha256: string;
  [key: string]: unknown;
}

export interface ProposalReview {
  proposal_id: string;
  run_status: string;
  graph: Record<string, unknown>;
  coverage: CallCoverageMatrix;
  approvals: ProposalApproval[];
  source_versions: ProposalSourceVersion[];
}

export interface HorizonEvaluation {
  prompt_version: string;
  generator_model?: string | null;
  evaluator_models: string[];
  criteria: {
    criterion: string;
    mean_score: number;
    disagreement: number;
    judge_results: {
      evaluator_model: string;
      score: number;
      strengths: string[];
      weaknesses: string[];
      recommendations: string[];
      reasoning: string;
    }[];
  }[];
  total_score: number;
  threshold_passed: boolean;
  coverage_percent: number;
  deterministic_blockers: string[];
  high_disagreement_criteria: string[];
}

export interface ProposalRenderRequest {
  content: string;
  content_format?: 'markdown' | 'html';
  metadata?: Record<string, unknown>;
  citation_registry?: Record<string, unknown>[];
  evidence_qa?: Record<string, unknown>;
  evidence_blockers?: string[];
  include_toc?: boolean;
  include_bibliography?: boolean;
  include_evidence_annex?: boolean;
  page_limit?: number | null;
  enforce_page_limit?: boolean;
}

export interface ProposalRenderResult {
  minio_key: string;
  pdf_key?: string;
  docx_key?: string;
  html_key?: string;
  source_html_key?: string;
  byte_size: number;
  page_count: number;
  estimated_page_count?: number;
  page_count_basis?: string;
  page_limit?: number | null;
  warnings: string[];
  submission_ready: boolean;
  template_used: string;
  template_version: string;
}
