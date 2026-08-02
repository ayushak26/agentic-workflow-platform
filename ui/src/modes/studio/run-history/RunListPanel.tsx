import { useMemo, useState } from 'react';
import type { RunStatus, RunSummary } from '../../../api/types';
import { StatusPill } from '../cockpit/node-render';
import { VirtualList } from '../cockpit/VirtualList';
import {
  RUN_STATUS_LABEL, filterRuns, groupRuns, relativeTime, sortRuns,
  type GroupBy, type RunFilters, type SortKey,
} from './run-list-utils';

const ROW_HEIGHT = 84;
const MAX_GROUP_LIST_HEIGHT = 420;

const STATUS_OPTIONS: RunStatus[] = ['running', 'paused', 'failed', 'completed', 'rejected'];
const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'recent', label: 'Most recent' },
  { key: 'oldest', label: 'Oldest' },
  { key: 'failed-first', label: 'Failed first' },
  { key: 'running-first', label: 'Running first' },
  { key: 'duration', label: 'Longest duration' },
  { key: 'workflow-name', label: 'Workflow name' },
];
const GROUP_OPTIONS: { key: GroupBy; label: string }[] = [
  { key: 'none', label: 'No grouping' },
  { key: 'date', label: 'Date' },
  { key: 'workflow', label: 'Workflow' },
  { key: 'status', label: 'Status' },
];

function shortRunId(runId: string): string {
  return runId.length > 8 ? `${runId.slice(0, 8)}…` : runId;
}

function progressFraction(run: RunSummary): number {
  if (!run.node_count) return 0;
  return Math.min(1, (run.completed_node_count ?? 0) / run.node_count);
}

function RunRow({ run, selected, onSelect }: { run: RunSummary; selected: boolean; onSelect: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? 'true' : undefined}
      className={`w-full h-full text-left px-3 py-2 border-l-2 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-500 ${
        selected
          ? 'bg-accent-50 border-l-accent-600'
          : 'border-l-transparent hover:bg-slate-50'
      }`}
    >
      <div className="flex items-center justify-between gap-2 min-w-0">
        <span className="text-sm font-medium text-ink-900 truncate min-w-0" title={run.workflow_name}>
          {run.workflow_name}
        </span>
        <StatusPill status={run.status} label={RUN_STATUS_LABEL[run.status] ?? run.status} />
      </div>
      <div className="mt-1 flex items-center gap-1.5 min-w-0 text-[11px] text-ink-500">
        {run.stage_id && (
          <span className="flex-none px-1.5 py-0.5 rounded bg-cyan-50 text-cyan-700 font-medium">
            {run.stage_id}
          </span>
        )}
        <span className="truncate">
          {relativeTime(run.started_at ?? Date.parse(run.created_at) / 1000)}
          {run.duration_s != null ? ` · ${run.duration_s.toFixed(0)}s` : ''}
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden">
          <div
            className={`h-full rounded-full ${run.status === 'failed' ? 'bg-bad' : 'bg-accent-600'}`}
            style={{ width: `${progressFraction(run) * 100}%` }}
          />
        </div>
        <span className="flex-none text-[10px] text-ink-500 font-mono">
          {run.completed_node_count ?? 0}/{run.node_count ?? '—'}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        {run.failed_node ? (
          <span className="text-[10px] text-bad truncate" title={`Failed at ${run.failed_node}`}>
            &#9888; {run.failed_node}
          </span>
        ) : <span />}
        <span
          role="button"
          tabIndex={-1}
          title={run.run_id}
          onClick={(e) => {
            e.stopPropagation();
            navigator.clipboard.writeText(run.run_id).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1200);
            }).catch(() => undefined);
          }}
          className="flex-none font-mono text-[10px] text-ink-400 hover:text-accent-600"
        >
          {copied ? 'copied' : shortRunId(run.run_id)}
        </span>
      </div>
    </button>
  );
}

export function RunListPanel({
  runs,
  listErr,
  selectedRunId,
  onSelect,
  onRefresh,
  collapsed,
  onToggleCollapsed,
}: {
  runs: RunSummary[];
  listErr: string | null;
  selectedRunId: string | undefined;
  onSelect: (runId: string) => void;
  onRefresh: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const [query, setQuery] = useState('');
  const [statuses, setStatuses] = useState<Set<RunStatus>>(new Set());
  const [hasErrors, setHasErrors] = useState(false);
  const [hasOutputs, setHasOutputs] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('recent');
  const [groupBy, setGroupBy] = useState<GroupBy>('none');
  const [filtersOpen, setFiltersOpen] = useState(false);

  const filters: RunFilters = useMemo(() => ({
    query, statuses, hasErrors: hasErrors || undefined, hasOutputs: hasOutputs || undefined,
  }), [query, statuses, hasErrors, hasOutputs]);

  const activeFilterCount = statuses.size + (hasErrors ? 1 : 0) + (hasOutputs ? 1 : 0);

  const visibleRuns = useMemo(
    () => sortRuns(filterRuns(runs, filters), sortKey),
    [runs, filters, sortKey],
  );
  const groups = useMemo(() => groupRuns(visibleRuns, groupBy), [visibleRuns, groupBy]);

  function resetFilters() {
    setStatuses(new Set());
    setHasErrors(false);
    setHasOutputs(false);
    setQuery('');
  }

  function toggleStatus(status: RunStatus) {
    setStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status); else next.add(status);
      return next;
    });
  }

  if (collapsed) {
    return (
      <div className="flex-none w-10 border-r border-slate-200 bg-white flex flex-col items-center py-3">
        <button
          type="button"
          onClick={onToggleCollapsed}
          title="Expand run history"
          className="text-ink-500 hover:text-ink-900 text-sm"
        >
          &#9656;
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-white">
      <div className="flex-none px-3 py-2.5 border-b border-slate-200 sticky top-0 bg-white z-10">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink-900">Run History</h2>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={onRefresh}
              title="Refresh"
              className="text-ink-500 hover:text-ink-900 text-xs px-1.5 py-1 rounded hover:bg-slate-100"
            >
              &#8635;
            </button>
            <button
              type="button"
              onClick={onToggleCollapsed}
              title="Collapse this panel"
              className="text-ink-400 hover:text-ink-700 text-xs px-1.5 py-1 rounded hover:bg-slate-100"
            >
              &#9666;
            </button>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search runs…"
            className="flex-1 min-w-0 rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            onClick={() => setFiltersOpen((v) => !v)}
            className={`flex-none px-2 py-1.5 rounded-md border text-xs ${
              activeFilterCount > 0 ? 'border-accent-500 bg-accent-50 text-accent-700' : 'border-slate-300 text-ink-700'
            }`}
          >
            Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
          </button>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            aria-label="Sort by"
            title="Sort by"
            className="flex-1 min-w-0 max-w-[50%] truncate rounded-md border border-slate-300 px-1.5 py-1 text-[11px]"
          >
            {SORT_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            aria-label="Group by"
            title="Group by"
            className="flex-1 min-w-0 max-w-[50%] truncate rounded-md border border-slate-300 px-1.5 py-1 text-[11px]"
          >
            {GROUP_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </div>
        {filtersOpen && (
          <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-2.5 space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {STATUS_OPTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggleStatus(s)}
                  className={`px-2 py-1 rounded text-[11px] border ${
                    statuses.has(s) ? 'border-accent-500 bg-accent-50 text-accent-700' : 'border-slate-300 text-ink-700'
                  }`}
                >
                  {RUN_STATUS_LABEL[s]}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-700">
              <input type="checkbox" checked={hasOutputs} onChange={(e) => setHasOutputs(e.target.checked)} />
              Runs with outputs
            </label>
            <label className="flex items-center gap-1.5 text-[11px] text-ink-700">
              <input type="checkbox" checked={hasErrors} onChange={(e) => setHasErrors(e.target.checked)} />
              Runs with errors
            </label>
            {activeFilterCount > 0 && (
              <button
                type="button"
                onClick={resetFilters}
                className="text-[11px] text-accent-600 hover:underline"
              >
                Reset all filters
              </button>
            )}
          </div>
        )}
      </div>

      {listErr && (
        <div className="m-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          Couldn't load runs. {listErr}
        </div>
      )}
      {!listErr && runs.length === 0 && (
        <div className="p-6 text-center text-ink-500 text-sm">No runs recorded yet.</div>
      )}
      {!listErr && runs.length > 0 && visibleRuns.length === 0 && (
        <div className="p-6 text-center text-ink-500 text-sm">No runs match your filters.</div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {groupBy === 'none' ? (
          <VirtualList
            items={visibleRuns}
            itemHeight={ROW_HEIGHT}
            className="h-full"
            renderItem={(run) => (
              <RunRow run={run} selected={run.run_id === selectedRunId} onSelect={() => onSelect(run.run_id)} />
            )}
          />
        ) : (
          groups.map((group) => (
            <div key={group.key}>
              <div className="sticky top-0 z-[5] px-3 py-1.5 bg-slate-100 text-[11px] font-medium text-ink-600">
                {group.label} <span className="text-ink-400 font-normal">({group.runs.length})</span>
              </div>
              <VirtualList
                items={group.runs}
                itemHeight={ROW_HEIGHT}
                height={Math.min(MAX_GROUP_LIST_HEIGHT, group.runs.length * ROW_HEIGHT)}
                renderItem={(run) => (
                  <RunRow run={run} selected={run.run_id === selectedRunId} onSelect={() => onSelect(run.run_id)} />
                )}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
