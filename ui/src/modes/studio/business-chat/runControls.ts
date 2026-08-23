import type { PauseKind, RunStatus } from '../../../api/types';

export type RunControlAction = 'pause' | 'resume' | 'retry' | 'restart';

export type RunControlInput = {
  status: RunStatus;
  pauseKind?: PauseKind;
  pausePending?: boolean;
  retryAvailable?: boolean;
  actionBusy?: RunControlAction | null;
};

export type RunControlState = {
  statusLabel: string;
  explanation: string | null;
  canPause: boolean;
  canResume: boolean;
  canRetry: boolean;
  canRestart: boolean;
  needsReview: boolean;
};

export function runControlState(input: RunControlInput): RunControlState {
  const busy = input.actionBusy != null;
  const needsReview = input.status === 'paused' && input.pauseKind !== 'user_requested';
  if (input.status === 'running') {
    return {
      statusLabel: input.pausePending ? 'Pause requested' : 'Workflow is running',
      explanation: input.pausePending
        ? 'The current step will finish before the workflow pauses at the next step boundary.'
        : null,
      canPause: !input.pausePending && !busy,
      canResume: false,
      canRetry: false,
      canRestart: false,
      needsReview: false,
    };
  }
  if (input.status === 'paused') {
    return {
      statusLabel: needsReview ? 'Review required' : 'Workflow paused',
      explanation: needsReview
        ? 'Respond to the review request to continue this attempt.'
        : 'Resume continues this same attempt from the paused step.',
      canPause: false,
      canResume: !needsReview && !busy,
      canRetry: false,
      canRestart: !busy,
      needsReview,
    };
  }
  if (input.status === 'failed') {
    return {
      statusLabel: 'Workflow failed',
      explanation: input.retryAvailable
        ? 'Retry reuses completed steps. Restart runs every step again.'
        : 'Restart runs every step again. This attempt has no reusable retry checkpoint.',
      canPause: false,
      canResume: false,
      canRetry: Boolean(input.retryAvailable) && !busy,
      canRestart: !busy,
      needsReview: false,
    };
  }
  return {
    statusLabel: input.status === 'completed' ? 'Workflow completed' : 'Workflow rejected',
    explanation: 'Restart creates a new attempt and runs every step again.',
    canPause: false,
    canResume: false,
    canRetry: false,
    canRestart: !busy,
    needsReview: false,
  };
}

export function attemptLabel(attempt?: number, reusedNodeCount?: number): string {
  const label = `Attempt ${attempt ?? 1}`;
  return reusedNodeCount && reusedNodeCount > 0
    ? `${label} · ${reusedNodeCount} step${reusedNodeCount === 1 ? '' : 's'} reused`
    : label;
}