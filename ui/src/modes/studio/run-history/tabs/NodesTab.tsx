import { useMemo, useState } from 'react';
import type { NodeRun, RunDetail } from '../../../../api/types';
import { STATUS_LABEL, type NodeStatus } from '../../cockpit-state';
import {
  StatusPill, clock, historicalNodeStatus, outputSummary, shortDuration, typeStyle,
} from '../../cockpit/node-render';
import { parseYaml } from '../../yaml-bridge';

type Row = {
  nodeId: string;
  typeName: string | undefined;
  status: NodeStatus;
  nodeRun: NodeRun | undefined;
};

const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed']);

function buildRows(run: RunDetail): Row[] {
  const nodeRunById = run.node_runs ?? {};
  const isTerminal = TERMINAL_STATUSES.has(run.status);
  let orderedIds: string[];
  try {
    orderedIds = run.workflow_yaml ? parseYaml(run.workflow_yaml).nodes.map((n) => n.id) : [];
  } catch {
    orderedIds = [];
  }
  const extraIds = Object.keys(nodeRunById).filter((id) => !orderedIds.includes(id));
  const allIds = [...orderedIds, ...extraIds];
  return allIds.map((nodeId) => {
    const nodeRun = nodeRunById[nodeId];
    return {
      nodeId,
      typeName: nodeRun?.type_name ?? run.node_types?.[nodeId],
      status: historicalNodeStatus(nodeId, nodeRunById, isTerminal),
      nodeRun,
    };
  });
}

/** Nodes whose recorded start times are within this many seconds of each
 * other are considered to have run concurrently, not sequentially. */
const CONCURRENCY_WINDOW_S = 1.5;

function concurrentGroupKey(rows: Row[], index: number): number {
  const started = rows[index].nodeRun?.started_at;
  if (started == null) return index;
  for (let i = 0; i < index; i += 1) {
    const other = rows[i].nodeRun?.started_at;
    if (other != null && Math.abs(other - started) <= CONCURRENCY_WINDOW_S) return i;
  }
  return index;
}

const STATUS_FILTERS: { key: NodeStatus | 'all'; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'failed', label: 'Failed only' },
  { key: 'active', label: 'Active only' },
];

export function NodesTab({
  run,
  selectedNodeId,
  onSelectNode,
}: {
  run: RunDetail;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<NodeStatus | 'all'>('all');
  const [withOutputsOnly, setWithOutputsOnly] = useState(false);
  const [withErrorsOnly, setWithErrorsOnly] = useState(false);

  const rows = useMemo(() => buildRows(run), [run]);
  const groupKeys = useMemo(() => rows.map((_, i) => concurrentGroupKey(rows, i)), [rows]);
  const concurrentCount = useMemo(() => {
    const counts = new Map<number, number>();
    for (const key of groupKeys) counts.set(key, (counts.get(key) ?? 0) + 1);
    return counts;
  }, [groupKeys]);

  const filtered = rows.filter((row, index) => {
    if (query && !row.nodeId.toLowerCase().includes(query.toLowerCase())
      && !(row.typeName ?? '').toLowerCase().includes(query.toLowerCase())) return false;
    if (statusFilter !== 'all' && row.status !== statusFilter) return false;
    if (withOutputsOnly && row.nodeRun?.output == null) return false;
    if (withErrorsOnly && !row.nodeRun?.error) return false;
    void index;
    return true;
  });

  return (
    <div className="p-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search nodes by name or type…"
          className="flex-1 min-w-[180px] rounded-md border border-slate-300 px-2.5 py-1.5 text-xs"
        />
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setStatusFilter(f.key)}
            className={`px-2.5 py-1.5 rounded-md border text-xs ${
              statusFilter === f.key ? 'border-accent-500 bg-accent-50 text-accent-700' : 'border-slate-300 text-ink-700'
            }`}
          >
            {f.label}
          </button>
        ))}
        <label className="flex items-center gap-1.5 text-xs text-ink-700">
          <input type="checkbox" checked={withOutputsOnly} onChange={(e) => setWithOutputsOnly(e.target.checked)} />
          With outputs
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-700">
          <input type="checkbox" checked={withErrorsOnly} onChange={(e) => setWithErrorsOnly(e.target.checked)} />
          With errors
        </label>
      </div>

      <div className="border border-slate-200 rounded-lg divide-y divide-slate-100 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="px-4 py-3 text-xs text-ink-500">No nodes match.</div>
        ) : (
          filtered.map((row) => {
            const ts = typeStyle(row.typeName);
            const index = rows.indexOf(row);
            const groupSize = concurrentCount.get(groupKeys[index]) ?? 1;
            return (
              <button
                key={row.nodeId}
                onClick={() => onSelectNode(row.nodeId)}
                className={`w-full text-left px-3.5 py-2.5 flex items-center gap-3 hover:bg-slate-50 ${
                  selectedNodeId === row.nodeId ? 'bg-accent-50' : ''
                }`}
              >
                <span className={`h-2.5 w-2.5 rounded-full flex-none ${ts.dot}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-sm text-ink-900">{row.nodeId}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${ts.chip}`}>{ts.label}</span>
                    <StatusPill status={row.status} label={STATUS_LABEL[row.status]} />
                    {groupSize > 1 && (
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-700"
                        title={`Ran concurrently with ${groupSize - 1} other node${groupSize - 1 === 1 ? '' : 's'}`}
                      >
                        &#8942; parallel &times;{groupSize}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-ink-500 flex items-center gap-2 flex-wrap">
                    {row.nodeRun?.started_at != null && <span>Started {clock(row.nodeRun.started_at)}</span>}
                    {row.nodeRun?.duration_s != null && <span>{shortDuration(row.nodeRun.duration_s)}</span>}
                    {row.nodeRun?.output != null && <span>{outputSummary(row.nodeRun.output)}</span>}
                  </div>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
