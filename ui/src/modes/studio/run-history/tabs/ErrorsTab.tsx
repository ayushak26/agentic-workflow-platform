import { useState } from 'react';
import type { NodeRun, RunDetail } from '../../../../api/types';
import { clock, suggestedCorrectiveAction } from '../../cockpit/node-render';

function ErrorCard({
  title, stage, message, errorType, traceback, ts, onInspect,
}: {
  title: string;
  stage?: string | null;
  message: string;
  errorType?: string | null;
  traceback?: string | null;
  ts?: number | string | null;
  onInspect?: () => void;
}) {
  const [showTechnical, setShowTechnical] = useState(false);
  const suggestion = suggestedCorrectiveAction(message);

  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-red-800">{title}</div>
        {ts != null && <div className="text-[11px] text-red-700">{clock(ts)}</div>}
      </div>
      {stage && <div className="mt-0.5 text-[11px] text-red-700">Stage: {stage}</div>}
      {errorType && (
        <div className="mt-1 inline-block text-[10px] font-mono px-1.5 py-0.5 rounded bg-white text-red-700 border border-red-200">
          {errorType}
        </div>
      )}
      <div className="mt-2 text-xs text-red-900">{message}</div>
      {suggestion && (
        <div className="mt-2 rounded-md border border-red-200 bg-white px-2.5 py-1.5 text-xs text-red-800">
          {suggestion}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        {onInspect && (
          <button
            onClick={onInspect}
            className="px-2.5 py-1 rounded-md border border-red-300 bg-white text-[11px] font-medium text-red-800 hover:bg-red-50"
          >
            Inspect node
          </button>
        )}
        {traceback && (
          <button
            onClick={() => setShowTechnical((v) => !v)}
            className="text-[11px] text-red-700 hover:underline"
          >
            {showTechnical ? 'Hide' : 'Show'} stack trace
          </button>
        )}
      </div>
      {showTechnical && traceback && (
        <pre className="mt-2 text-[10px] bg-white border border-red-200 rounded-md p-2 overflow-auto max-h-56 whitespace-pre-wrap font-mono text-red-900">
          {traceback}
        </pre>
      )}
    </div>
  );
}

export function ErrorsTab({
  run,
  onInspectNode,
}: {
  run: RunDetail;
  onInspectNode: (nodeId: string) => void;
}) {
  const nodeRuns = Object.values(run.node_runs ?? {}) as NodeRun[];
  const failedNodes = nodeRuns.filter((n) => n.error);
  const hasRunLevelError = Boolean(run.error) && !failedNodes.some((n) => n.error === run.error);

  if (!hasRunLevelError && failedNodes.length === 0) {
    return <div className="p-6 text-sm text-ink-500">No errors recorded for this run.</div>;
  }

  return (
    <div className="p-4 space-y-3">
      {run.status === 'failed' && (
        <div className="text-xs text-ink-500">
          {run.retry_available
            ? 'This run is retryable — completed nodes will be reused.'
            : 'This run predates retry checkpoints and cannot resume from a checkpoint.'}
        </div>
      )}
      {hasRunLevelError && (
        <ErrorCard title="Run-level failure" message={run.error ?? ''} />
      )}
      {failedNodes.map((nodeRun) => (
        <ErrorCard
          key={nodeRun.node_id}
          title={`Node: ${nodeRun.node_id}`}
          message={nodeRun.error ?? ''}
          errorType={nodeRun.error_type}
          traceback={nodeRun.error_traceback}
          ts={nodeRun.ended_at}
          onInspect={() => onInspectNode(nodeRun.node_id)}
        />
      ))}
    </div>
  );
}
