import { useMemo, useState } from 'react';
import type { AuditEvent, NodeRun, RunDetail } from '../../../../api/types';
import { computeStatusCounts, STATUS_LABEL } from '../../cockpit-state';
import { historicalNodeStatus, suggestedCorrectiveAction } from '../../cockpit/node-render';
import { parseYaml } from '../../yaml-bridge';
import { mergeNodeEvents } from '../timeline-utils';

const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed']);

function allNodeIds(run: RunDetail): string[] {
  if (!run.workflow_yaml) return Object.keys(run.node_runs ?? {});
  try {
    return parseYaml(run.workflow_yaml).nodes.map((n) => n.id);
  } catch {
    return Object.keys(run.node_runs ?? {});
  }
}

function FailureCard({
  run, failedNodeRun, onInspectNode, onRetry,
}: {
  run: RunDetail;
  failedNodeRun: NodeRun | undefined;
  onInspectNode: (nodeId: string) => void;
  onRetry: () => void;
}) {
  const failedNode = run.failed_node ?? failedNodeRun?.node_id;
  const totalCompleted = run.completed_node_count ?? 0;
  const total = run.node_count ?? 0;
  const reused = run.retryable_node_count ?? 0;
  const [showTechnical, setShowTechnical] = useState(false);
  const rawError = failedNodeRun?.error ?? run.error ?? '';
  const suggestion = suggestedCorrectiveAction(rawError);

  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-red-800">
          Failed{failedNode ? ` at ${failedNode}` : ''}
        </div>
        {failedNodeRun?.ended_at != null && (
          <div className="text-[11px] text-red-700">
            {new Date(failedNodeRun.ended_at * 1000).toLocaleString()}
          </div>
        )}
      </div>

      <ul className="mt-3 space-y-1 text-xs text-red-900 list-disc pl-4">
        <li>The worker process stopped unexpectedly, or the node itself raised an error.</li>
        <li>The run completed {totalCompleted} of {total} nodes.</li>
        <li>Completed outputs are still available for inspection below.</li>
        <li>
          {run.retry_available
            ? `Retry will reuse ${reused} completed node${reused === 1 ? '' : 's'} and resume from the failed section.`
            : 'A reusable checkpoint is not available for this older run — retry will start over.'}
        </li>
      </ul>

      {suggestion && (
        <div className="mt-3 rounded-md border border-red-200 bg-white px-3 py-2 text-xs text-red-800">
          {suggestion}
        </div>
      )}

      <div className="mt-3 flex items-center gap-2">
        {failedNode && (
          <button
            onClick={() => onInspectNode(failedNode)}
            className="px-3 py-1.5 rounded-md border border-red-300 bg-white text-xs font-medium text-red-800 hover:bg-red-50"
          >
            Inspect failed node
          </button>
        )}
        <button
          onClick={onRetry}
          className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500"
        >
          Retry from failure
        </button>
        <button
          onClick={() => setShowTechnical((v) => !v)}
          className="ml-auto text-xs text-red-700 hover:underline"
        >
          {showTechnical ? 'Hide' : 'Show'} technical details
        </button>
      </div>

      {showTechnical && (
        <pre className="mt-2 text-[11px] bg-white border border-red-200 rounded-md p-2 overflow-auto max-h-48 whitespace-pre-wrap font-mono text-red-900">
          {rawError || 'No technical message recorded.'}
        </pre>
      )}
    </div>
  );
}

export function OverviewTab({
  run,
  audit,
  onInspectNode,
  onRetry,
}: {
  run: RunDetail;
  audit: AuditEvent[];
  onInspectNode: (nodeId: string) => void;
  onRetry: () => void;
}) {
  const isTerminal = TERMINAL_STATUSES.has(run.status);
  const nodeIds = useMemo(() => allNodeIds(run), [run]);
  const nodeRunById = useMemo(() => run.node_runs ?? {}, [run.node_runs]);
  const counts = useMemo(() => computeStatusCounts(
    Object.fromEntries(nodeIds.map((id) => [id, historicalNodeStatus(id, nodeRunById, isTerminal)])),
  ), [nodeIds, nodeRunById, isTerminal]);

  const latestEvent = useMemo(() => mergeNodeEvents(audit).at(-1), [audit]);
  const outputCount = Object.keys(run.outputs ?? {}).length;
  const failedNodeRun = run.failed_node ? nodeRunById[run.failed_node] : undefined;

  return (
    <div className="p-4 space-y-4">
      {run.status === 'failed' && (
        <FailureCard run={run} failedNodeRun={failedNodeRun} onInspectNode={onInspectNode} onRetry={onRetry} />
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        {[
          { label: 'Completed', value: counts.completed, tone: 'text-emerald-700' },
          { label: 'Running', value: counts.running, tone: 'text-blue-700' },
          { label: 'Failed', value: counts.failed, tone: 'text-red-700' },
          { label: 'Skipped', value: counts.skipped + counts.cancelled, tone: 'text-ink-500' },
        ].map((m) => (
          <div key={m.label} className="rounded-lg border border-slate-200 px-3 py-2.5">
            <div className="text-[11px] text-ink-500">{m.label}</div>
            <div className={`text-lg font-semibold ${m.tone}`}>{m.value}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
        <div className="rounded-lg bg-slate-50 px-3 py-2.5">
          <div className="text-[11px] text-ink-500 mb-1">Total duration</div>
          <div className="text-sm font-medium text-ink-900">
            {run.duration_s != null ? `${run.duration_s.toFixed(1)}s` : '—'}
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 px-3 py-2.5">
          <div className="text-[11px] text-ink-500 mb-1">Generated outputs</div>
          <div className="text-sm font-medium text-ink-900">{outputCount} node{outputCount === 1 ? '' : 's'}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-ink-500 mb-1.5">Latest meaningful event</div>
        {latestEvent ? (
          <div className="rounded-md border border-slate-200 px-3 py-2 text-xs text-ink-700">
            {latestEvent.kind === 'node' ? (
              <>Node <span className="font-mono">{latestEvent.nodeId}</span> {STATUS_LABEL[
                latestEvent.status === 'completed' ? 'done' : latestEvent.status === 'failed' ? 'failed' : 'reused'
              ].toLowerCase()}</>
            ) : (
              <>Human decision ({latestEvent.eventType.replace('hitl_', '')}) by {latestEvent.actor}</>
            )}
          </div>
        ) : (
          <div className="text-xs text-ink-500">No events recorded yet.</div>
        )}
      </div>
    </div>
  );
}
