import { useEffect, useMemo, useState } from 'react';
import { STATUS_LABEL, computeStatusCounts, type NodeStatus, type StatusCounts } from '../cockpit-state';
import { typeStyle } from './node-render';
import { VirtualList } from './VirtualList';
import type { RunCostSummary } from '../../../api/types';

export type OverviewNode = {
  id: string;
  typeName: string;
  status: NodeStatus;
};

const FILTER_OPTIONS: Array<{ key: keyof StatusCounts; label: string }> = [
  { key: 'running', label: 'Running' },
  { key: 'completed', label: 'Completed' },
  { key: 'waiting', label: 'Waiting' },
  { key: 'paused', label: 'Paused' },
  { key: 'failed', label: 'Failed' },
  { key: 'skipped', label: 'Skipped' },
  { key: 'cancelled', label: 'Cancelled' },
];

const STATUS_TO_FILTER: Record<NodeStatus, keyof StatusCounts | null> = {
  pending: 'waiting',
  active: 'running',
  done: 'completed',
  reused: 'completed',
  paused: 'paused',
  failed: 'failed',
  skipped: 'skipped',
  cancelled: 'cancelled',
};

function costLabel(usd: number): string {
  return usd > 0 && usd < 0.0001 ? '<$0.0001' : `$${usd.toFixed(4)}`;
}

// §31/§33: workflow-level AI cost, broken down by node and (when present) by
// RAG pipeline stage — collapsed by default so it never competes with the
// node list for attention.
function AICostSummary({
  costSummary,
  onSelectNode,
}: {
  costSummary: RunCostSummary;
  onSelectNode: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const nodeRows = useMemo(
    () => [...costSummary.by_node_summary].sort((a, b) => b.cost_usd - a.cost_usd),
    [costSummary.by_node_summary],
  );
  const stageRows = costSummary.by_stage;

  return (
    <div className="flex-none px-4 py-3 border-b border-slate-200 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between"
      >
        <span className="font-medium text-ink-900">{expanded ? '▾' : '▸'} AI Cost</span>
        <span className="font-semibold text-ink-900">{costLabel(costSummary.total_usd)}</span>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2">
          {nodeRows.length > 0 && (
            <div className="space-y-0.5">
              {nodeRows.map((row) => (
                <button
                  key={row.node_id}
                  type="button"
                  onClick={() => row.node_id && onSelectNode(row.node_id)}
                  className="w-full flex items-center justify-between rounded px-1.5 py-1 hover:bg-slate-50 text-left"
                >
                  <span className="font-mono text-ink-700 truncate">{row.node_id}</span>
                  <span className="text-ink-900 flex-none ml-2">
                    {row.no_model_charge ? 'No charge' : costLabel(row.cost_usd)}
                  </span>
                </button>
              ))}
            </div>
          )}
          {stageRows.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-ink-500 mt-2 mb-1">
                Knowledge / RAG cost
              </div>
              {stageRows.map((row) => (
                <div key={row.stage} className="flex items-center justify-between px-1.5 py-0.5">
                  <span className="text-ink-700 capitalize">{row.stage?.replace(/_/g, ' ')}</span>
                  <span className="text-ink-900">
                    {row.no_model_charge ? 'No model charge' : costLabel(row.cost_usd)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function elapsedLabel(startedAt: number | null, endedAt: number | null): string {
  if (startedAt == null) return '—';
  const end = endedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, end - startedAt);
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const minutes = Math.floor(seconds / 60);
  const remSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remSeconds}s`;
}

export function OverviewPanel({
  workflowName,
  runStatus,
  startedAt,
  endedAt,
  nodes,
  selectedId,
  onSelect,
  collapsed,
  onToggleCollapsed,
  costSummary = null,
}: {
  workflowName: string;
  runStatus: string;
  startedAt: number | null;
  endedAt: number | null;
  nodes: OverviewNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  // Present once the run completes and /api/cost/run/{id} resolves.
  costSummary?: RunCostSummary | null;
}) {
  const [query, setQuery] = useState('');
  const [activeFilters, setActiveFilters] = useState<Set<keyof StatusCounts>>(new Set());
  // Ticks the "total execution time" readout once a second while the run is
  // still going — a plain interval, not tied to any status recalculation.
  const [, forceTick] = useState(0);
  useEffect(() => {
    if (endedAt != null) return;
    const timer = window.setInterval(() => forceTick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, [endedAt]);

  const counts = useMemo(
    () => computeStatusCounts(Object.fromEntries(nodes.map((n) => [n.id, n.status]))),
    [nodes],
  );

  const filtered = useMemo(() => {
    const lowerQuery = query.trim().toLowerCase();
    return nodes.filter((n) => {
      if (lowerQuery && !n.id.toLowerCase().includes(lowerQuery) && !n.typeName.toLowerCase().includes(lowerQuery)) {
        return false;
      }
      if (activeFilters.size > 0) {
        const bucket = STATUS_TO_FILTER[n.status];
        if (!bucket || !activeFilters.has(bucket)) return false;
      }
      return true;
    });
  }, [nodes, query, activeFilters]);

  function toggleFilter(key: keyof StatusCounts) {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  if (collapsed) {
    return (
      <div className="flex-none w-10 border-r border-slate-200 bg-white flex flex-col items-center py-3">
        <button
          type="button"
          onClick={onToggleCollapsed}
          title="Expand workflow overview"
          className="text-ink-500 hover:text-ink-900 text-sm"
        >
          ▸
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-white">
      <div className="flex-none px-4 py-3 border-b border-slate-200">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="font-semibold text-ink-900 truncate" title={workflowName}>{workflowName}</div>
            <div className="text-xs text-ink-500 mt-0.5 capitalize">{runStatus}</div>
          </div>
          <button
            type="button"
            onClick={onToggleCollapsed}
            title="Collapse this panel"
            className="flex-none text-ink-400 hover:text-ink-700 text-sm"
          >
            ◂
          </button>
        </div>
        <div className="mt-2 text-[11px] text-ink-500">
          {startedAt != null && (
            <span>Started {new Date(startedAt * 1000).toLocaleTimeString()} · </span>
          )}
          <span>{elapsedLabel(startedAt, endedAt)} elapsed</span>
        </div>
      </div>

      {costSummary != null && (costSummary.total_usd > 0 || costSummary.by_node.length > 0) && (
        <AICostSummary costSummary={costSummary} onSelectNode={onSelect} />
      )}

      <div className="flex-none px-4 py-3 border-b border-slate-200">
        <div className="grid grid-cols-2 gap-1.5 text-[11px]">
          {FILTER_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => toggleFilter(key)}
              className={`flex items-center justify-between rounded px-2 py-1 border ${
                activeFilters.has(key)
                  ? 'border-accent-500 bg-accent-50 text-accent-800'
                  : 'border-slate-200 text-ink-600 hover:bg-slate-50'
              }`}
            >
              <span>{label}</span>
              <span className="font-semibold">{counts[key]}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex-none px-4 pt-3 pb-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search nodes by name or type…"
          className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-xs"
        />
        <div className="mt-1.5 text-[11px] text-ink-400">
          {filtered.length} of {nodes.length} node{nodes.length === 1 ? '' : 's'}
        </div>
      </div>

      <div className="flex-1 min-h-0 px-2 pb-2">
        <VirtualList
          items={filtered}
          itemHeight={52}
          className="h-full"
          emptyState={<div className="px-3 py-4 text-xs text-ink-500">No nodes match.</div>}
          renderItem={(node) => {
            const ts = typeStyle(node.typeName);
            const on = node.id === selectedId;
            return (
              <button
                type="button"
                onClick={() => onSelect(node.id)}
                className={`w-full h-full flex flex-col justify-center px-2.5 rounded-md text-left ${
                  on ? 'bg-accent-50 ring-1 ring-accent-300' : 'hover:bg-slate-50'
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`h-2 w-2 rounded-full flex-none ${ts.dot}`} />
                  <span className="font-mono text-xs text-ink-900 truncate">{node.id}</span>
                </div>
                <div className="text-[11px] text-ink-500 mt-0.5 pl-4">
                  {ts.label} · {STATUS_LABEL[node.status]}
                </div>
              </button>
            );
          }}
        />
      </div>
    </div>
  );
}
