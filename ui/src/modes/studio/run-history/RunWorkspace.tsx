import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { AuditEvent, RunDetail } from '../../../api/types';
import { CopyButton } from '../../../components/CopyButton';
import { clock, StatusPill } from '../cockpit/node-render';
import { RUN_STATUS_LABEL } from './run-list-utils';
import { deleteBlockedReason } from './useRunHistoryData';

export type WorkspaceTab = 'overview' | 'nodes' | 'outputs' | 'inputs' | 'timeline' | 'errors' | 'ask-ai';

const TABS: { key: WorkspaceTab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'nodes', label: 'Nodes' },
  { key: 'outputs', label: 'Outputs' },
  { key: 'inputs', label: 'Inputs' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'errors', label: 'Errors' },
  { key: 'ask-ai', label: 'Ask AI' },
];

function durationLabel(run: RunDetail): string {
  if (run.duration_s != null) return `${run.duration_s.toFixed(0)}s`;
  if (run.started_at != null && run.status === 'running') {
    return `${Math.max(0, Date.now() / 1000 - run.started_at).toFixed(0)}s so far`;
  }
  return '—';
}

function buildReusableInputsJson(run: RunDetail): string {
  const nodeRunById = Object.fromEntries(
    Object.values(run.node_runs ?? {}).map((record) => [record.node_id, record]),
  );
  const nodeIds = Array.from(new Set([...Object.keys(nodeRunById), ...Object.keys(run.outputs ?? {})]));
  const nodeOutputs = Object.fromEntries(
    nodeIds
      .map((id) => [id, nodeRunById[id]?.output ?? run.outputs?.[id]])
      .filter(([, value]) => value != null),
  );
  return JSON.stringify({ ...run.inputs, ...nodeOutputs }, null, 2);
}

function downloadJson(value: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function RunWorkspace({
  detail,
  actionBusy,
  actionErr,
  retryErr,
  autofixErr,
  blockingPipelineId,
  onPause,
  onResume,
  onRestart,
  onDelete,
  onAbandonAndDelete,
  onRetry,
  onOpenInCockpit,
  onOpenInBusinessView,
  onAutofix,
  autofixBusy,
  onOpenProposalReview,
  onOpenEvidence,
  activeTab,
  onTabChange,
  children,
}: {
  detail: { run: RunDetail; audit: AuditEvent[] };
  actionBusy: 'pause' | 'resume' | 'restart' | 'delete' | null;
  actionErr: string | null;
  retryErr: string | null;
  autofixErr?: string | null;
  blockingPipelineId?: string | null;
  onPause: () => void;
  onResume: () => void;
  onRestart: () => void;
  onDelete: () => void;
  onAbandonAndDelete?: () => void;
  onRetry: () => void;
  onOpenInCockpit: () => void;
  onOpenInBusinessView: () => void;
  onAutofix: () => void;
  autofixBusy: boolean;
  onOpenProposalReview: () => void;
  onOpenEvidence: () => void;
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  children: ReactNode;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement>(null);
  const run = detail.run;
  const deleteBlocked = deleteBlockedReason(run);

  useEffect(() => {
    if (!moreOpen) return;
    function onPointerDown(e: MouseEvent) {
      if (moreMenuRef.current && !moreMenuRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [moreOpen]);

  const isHitlGatePause = run.status === 'paused' && run.pause_kind !== 'user_requested';

  return (
    <div className="flex flex-col h-full min-h-0 min-w-0">
      <div className="flex-none border-b border-slate-200 bg-white px-4 py-3 sticky top-0 z-10">
        <div className="flex flex-wrap items-start justify-between gap-3 min-w-0">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 min-w-0">
              <span className="text-base font-semibold text-ink-900 truncate" title={run.workflow_name}>
                {run.workflow_name}
              </span>
              {run.stage_id && (
                <span className="flex-none px-1.5 py-0.5 rounded bg-cyan-50 text-cyan-700 text-[11px] font-medium">
                  {run.stage_id}{run.stage_index != null && run.total_stages ? ` (${run.stage_index + 1}/${run.total_stages})` : ''}
                </span>
              )}
              <StatusPill status={run.status} label={RUN_STATUS_LABEL[run.status] ?? run.status} />
              {(run.attempt ?? 1) > 1 && (
                <span className="flex-none text-[11px] text-cyan-700">Attempt {run.attempt}</span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-500">
              <span>{run.completed_node_count ?? 0}/{run.node_count ?? '—'} nodes</span>
              <span>Started {clock(run.started_at ?? run.created_at)}</span>
              <span>Duration {durationLabel(run)}</span>
              <span>Updated {clock(run.updated_at)}</span>
              <span className="inline-flex items-center gap-1">
                <span className="font-mono">{run.run_id.slice(0, 8)}…</span>
                <CopyButton text={run.run_id} label="Copy ID" className="text-[9px]" />
              </span>
            </div>
          </div>

          <div className="flex-none flex flex-wrap items-start gap-2 max-w-full">
            {/* Primary action — exactly one, status-dependent. */}
            {isHitlGatePause ? (
              <button
                onClick={onOpenInCockpit}
                className="px-3 py-1.5 rounded-md bg-amber-600 text-white text-xs font-medium hover:bg-amber-500"
              >
                Review &amp; respond
              </button>
            ) : run.status === 'running' ? (
              <button
                onClick={onPause}
                disabled={actionBusy !== null}
                className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500 disabled:opacity-50"
              >
                {actionBusy === 'pause' ? 'Pausing…' : 'Pause'}
              </button>
            ) : run.status === 'paused' ? (
              <button
                onClick={onResume}
                disabled={actionBusy !== null}
                className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500 disabled:opacity-50"
              >
                {actionBusy === 'resume' ? 'Resuming…' : 'Resume'}
              </button>
            ) : run.status === 'failed' ? (
              <button
                onClick={onRetry}
                className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500"
              >
                Retry from failure
              </button>
            ) : run.status === 'completed' ? (
              <button
                onClick={() => onTabChange('outputs')}
                className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500"
              >
                View outputs
              </button>
            ) : null}

            {/* Secondary actions. */}
            {run.workflow_yaml && (
              <>
                <button
                  onClick={onOpenInBusinessView}
                  className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50"
                >
                  Open Business View
                </button>
                <button
                  onClick={onOpenInCockpit}
                  className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50"
                >
                  Open in Cockpit
                </button>
                <button
                  onClick={onAutofix}
                  disabled={autofixBusy}
                  className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {autofixBusy ? 'Fixing…' : 'Auto-fix'}
                </button>
              </>
            )}
            <button
              onClick={onOpenProposalReview}
              className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50"
            >
              Proposal review
            </button>
            <button
              onClick={onOpenEvidence}
              className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50"
            >
              Evidence
            </button>

            {/* Overflow menu. */}
            <div className="relative" ref={moreMenuRef}>
              <button
                onClick={() => setMoreOpen((v) => !v)}
                className="px-3 py-1.5 rounded-md border border-slate-300 text-xs text-ink-700 hover:bg-slate-50"
              >
                More &#8964;
              </button>
              {moreOpen && (
                <div className="absolute right-0 mt-1 w-56 rounded-md border border-slate-200 bg-white shadow-lg z-20 py-1">
                  <button
                    onClick={() => { navigator.clipboard.writeText(buildReusableInputsJson(run)).catch(() => undefined); setMoreOpen(false); }}
                    className="w-full text-left px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50"
                  >
                    Copy run as workflow inputs
                  </button>
                  <button
                    onClick={() => { navigator.clipboard.writeText(run.run_id).catch(() => undefined); setMoreOpen(false); }}
                    className="w-full text-left px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50"
                  >
                    Copy run ID
                  </button>
                  <button
                    onClick={() => { navigator.clipboard.writeText(JSON.stringify(run, null, 2)).catch(() => undefined); setMoreOpen(false); }}
                    className="w-full text-left px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50"
                  >
                    Copy as JSON
                  </button>
                  <button
                    onClick={() => { onRestart(); setMoreOpen(false); }}
                    disabled={actionBusy !== null}
                    className="w-full text-left px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {actionBusy === 'restart' ? 'Restarting…' : 'Restart from beginning'}
                  </button>
                  <button
                    onClick={() => { downloadJson(detail.audit, `${run.run_id}-audit.json`); setMoreOpen(false); }}
                    className="w-full text-left px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50"
                  >
                    Export audit data
                  </button>
                  <div className="my-1 border-t border-slate-100" />
                  {confirmingDelete ? (
                    <div className="px-3 py-1.5">
                      <div className="text-[11px] text-ink-700 mb-1.5">Delete this run permanently?</div>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => { onDelete(); setConfirmingDelete(false); setMoreOpen(false); }}
                          disabled={actionBusy !== null || deleteBlocked !== null}
                          title={deleteBlocked ?? undefined}
                          className="flex-1 px-2 py-1 rounded bg-red-600 text-white text-[11px] font-medium hover:bg-red-500 disabled:opacity-50"
                        >
                          {actionBusy === 'delete' ? 'Deleting…' : 'Confirm delete'}
                        </button>
                        <button
                          onClick={() => setConfirmingDelete(false)}
                          className="flex-1 px-2 py-1 rounded border border-slate-300 text-[11px] text-ink-700 hover:bg-slate-50"
                        >
                          Cancel
                        </button>
                      </div>
                      {deleteBlocked && <div className="mt-1 text-[10px] text-ink-500">{deleteBlocked}</div>}
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmingDelete(true)}
                      disabled={deleteBlocked !== null}
                      title={deleteBlocked ?? undefined}
                      className="w-full text-left px-3 py-1.5 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                    >
                      Delete run…
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {(actionErr || retryErr || autofixErr) && (
          <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
            <div>{actionErr ?? retryErr ?? autofixErr}</div>
            {actionErr && blockingPipelineId && onAbandonAndDelete && (
              <button
                onClick={onAbandonAndDelete}
                disabled={actionBusy !== null}
                className="mt-1.5 px-2 py-1 rounded bg-red-600 text-white text-[11px] font-medium hover:bg-red-500 disabled:opacity-50"
              >
                {actionBusy === 'delete' ? 'Abandoning & deleting…' : 'Abandon pipeline & delete this run'}
              </button>
            )}
          </div>
        )}

        <div className="mt-3 flex items-center gap-1 -mb-3 overflow-x-auto">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => onTabChange(key)}
              className={`px-3 py-2 text-xs font-medium whitespace-nowrap border-b-2 ${
                activeTab === key ? 'border-accent-600 text-ink-900' : 'border-transparent text-ink-500 hover:text-ink-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 min-w-0 overflow-y-auto">
        {children}
      </div>
    </div>
  );
}
