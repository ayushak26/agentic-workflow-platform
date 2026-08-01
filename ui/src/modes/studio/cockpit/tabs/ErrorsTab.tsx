import type { NavigateFunction } from 'react-router-dom';
import type { NodeRun, RunDetail } from '../../../../api/types';
import { startRetryRun, suggestedCorrectiveAction } from '../node-render';

export function ErrorsTab({
  nodeRun,
  run,
  navigate,
}: {
  nodeRun: NodeRun | undefined;
  run: RunDetail | null;
  navigate: NavigateFunction;
}) {
  if (!nodeRun?.error) {
    return <div className="p-4 text-sm text-ink-500">No error recorded for this node.</div>;
  }

  const suggestion = suggestedCorrectiveAction(nodeRun.error);
  const canRetry = run != null && run.status === 'failed' && Boolean(run.retry_available);

  return (
    <div className="p-3 space-y-3">
      <div className="rounded-md border border-red-200 bg-red-50 p-3">
        <div className="text-[11px] uppercase tracking-wide text-red-700 mb-1">Message</div>
        <div className="text-sm text-red-800 font-mono whitespace-pre-wrap">{nodeRun.error}</div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Type</div>
          <div className="font-mono text-ink-900">{nodeRun.error_type ?? 'Unknown'}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Failed operation</div>
          <div className="font-mono text-ink-900">{nodeRun.type_name}</div>
        </div>
      </div>

      {suggestion && (
        <div className="rounded-md border border-accent-200 bg-accent-50 p-3 text-xs text-accent-900">
          <div className="font-medium mb-1">Suggested next step</div>
          {suggestion}
        </div>
      )}

      {nodeRun.error_traceback && (
        <details className="rounded-md border border-slate-200">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-ink-700">
            Stack trace
          </summary>
          <pre className="text-[11px] bg-slate-50 border-t border-slate-200 p-3 overflow-auto max-h-72 whitespace-pre-wrap">
            {nodeRun.error_traceback}
          </pre>
        </details>
      )}

      {canRetry && run && (
        <button
          type="button"
          onClick={() => startRetryRun(run, navigate)}
          className="w-full px-3 py-2 rounded-md bg-accent-600 text-white text-sm font-medium hover:bg-accent-500"
        >
          Retry from this failure
        </button>
      )}
    </div>
  );
}
