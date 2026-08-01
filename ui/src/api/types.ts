// Mirrors app/runtime/schema.py and app/nodes/registry.py manifest output.
export type NodeTypeManifest = {
  type_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  config_schema: Record<string, unknown>;
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

export type WorkflowSummary = {
  name: string;
  description: string;
  node_count: number;
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
  model_selections?: ModelSelection[];
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

export type PipelineRunStatus = 'running' | 'gated' | 'completed' | 'failed';

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
