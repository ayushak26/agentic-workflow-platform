import type { RunDetail } from '../../../api/types';
import { attemptLabel, runControlState, type RunControlAction } from './runControls';

export function RunControlBar({
  run,
  pausePending,
  actionBusy,
  error,
  onPause,
  onResume,
  onRetry,
  onRestart,
}: {
  run: RunDetail;
  pausePending: boolean;
  actionBusy: RunControlAction | null;
  error: string | null;
  onPause: () => void;
  onResume: () => void;
  onRetry: () => void;
  onRestart: () => void;
}) {
  const state = runControlState({
    status: run.status,
    pauseKind: run.pause_kind,
    pausePending,
    retryAvailable: run.retry_available,
    actionBusy,
  });
  return (
    <div className="border-b border-slate-200 bg-slate-50 px-3 py-2" aria-label="Run controls">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-ink-800">{state.statusLabel}</span>
            <span className="text-ink-400">{attemptLabel(run.attempt, run.reused_node_count)}</span>
          </div>
          {state.explanation && <p className="mt-0.5 text-[11px] text-ink-500">{state.explanation}</p>}
        </div>
        <div className="flex items-center gap-2">
          {(state.canPause || actionBusy === 'pause') && (
            <button type="button" onClick={onPause} disabled={actionBusy != null} className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-slate-100 disabled:opacity-50">
              {actionBusy === 'pause' ? 'Requesting pause…' : 'Pause'}
            </button>
          )}
          {(state.canResume || actionBusy === 'resume') && (
            <button type="button" onClick={onResume} disabled={actionBusy != null} className="rounded-md bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-50">
              {actionBusy === 'resume' ? 'Resuming…' : 'Resume'}
            </button>
          )}
          {state.needsReview && <span className="text-xs font-medium text-amber-700">Use the review card below</span>}
          {(state.canRetry || actionBusy === 'retry') && (
            <button type="button" onClick={onRetry} disabled={actionBusy != null} className="rounded-md bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-50">
              {actionBusy === 'retry' ? 'Retrying…' : 'Retry with completed steps'}
            </button>
          )}
          {(state.canRestart || actionBusy === 'restart') && (
            <button type="button" onClick={onRestart} disabled={actionBusy != null} className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-slate-100 disabled:opacity-50">
              {actionBusy === 'restart' ? 'Restarting…' : 'Restart from beginning'}
            </button>
          )}
        </div>
      </div>
      {error && <p className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}