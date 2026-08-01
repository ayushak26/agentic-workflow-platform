import { useEffect, useMemo, useState } from 'react';
import { STATUS_LABEL, computeStatusCounts, type NodeStatus, type StatusCounts } from '../cockpit-state';
import { typeStyle } from './node-render';
import { VirtualList } from './VirtualList';

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
