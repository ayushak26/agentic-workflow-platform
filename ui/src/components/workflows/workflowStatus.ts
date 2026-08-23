import type { NodeRunStatus, RunStatus } from '../../api/types';

export type WorkflowStatus = 'pending' | 'running' | 'done' | 'error' | 'paused';

export const WORKFLOW_STATUS_LABEL: Record<WorkflowStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  done: 'Done',
  error: 'Error',
  paused: 'Paused',
};

export function normalizeWorkflowStatus(status?: string | null): WorkflowStatus {
  if (!status || status === 'waiting' || status === 'pending' || status === 'skipped') return 'pending';
  if (status === 'running' || status === 'active' || status === 'connecting') return 'running';
  if (status === 'completed' || status === 'done' || status === 'reused' || status === 'successful') return 'done';
  if (status === 'paused' || status === 'needs_input' || status === 'gated') return 'paused';
  return 'error';
}

export function workflowStatusFromNode(status?: NodeRunStatus | null): WorkflowStatus {
  return normalizeWorkflowStatus(status);
}

export function workflowStatusFromRun(status?: RunStatus | null): WorkflowStatus {
  return normalizeWorkflowStatus(status);
}

export const WORKFLOW_STATUS_GLYPH: Record<WorkflowStatus, string> = {
  pending: '○',
  running: '●',
  done: '✓',
  error: '×',
  paused: '!',
};

export const WORKFLOW_STATUS_CLASS: Record<WorkflowStatus, string> = {
  pending: 'text-ink-400',
  running: 'text-accent-700',
  done: 'text-ok',
  error: 'text-bad',
  paused: 'text-amber-700',
};