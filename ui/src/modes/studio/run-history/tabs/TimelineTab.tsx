import { useMemo, useState } from 'react';
import type { AuditEvent } from '../../../../api/types';
import { clock } from '../../cockpit/node-render';
import { mergeNodeEvents, type TimelineRow } from '../timeline-utils';

const EVENT_TYPE_LABEL: Record<string, string> = {
  node_start: 'Node started',
  node_end: 'Node completed',
  node_reused: 'Node reused (zero tokens)',
  node_error: 'Node error',
  hitl_approve: 'Approved',
  hitl_reject: 'Rejected',
  hitl_edit: 'Edited',
};

function rowNodeId(row: TimelineRow): string | null {
  return row.nodeId;
}

function rowTs(row: TimelineRow): string {
  return row.kind === 'node' ? row.endTs : row.ts;
}

function rowMatchesQuery(row: TimelineRow, query: string): boolean {
  if (!query) return true;
  const needle = query.toLowerCase();
  const haystack = [
    rowNodeId(row) ?? '',
    row.kind === 'node' ? row.status : row.eventType,
    row.actor,
    row.reason ?? '',
  ].join(' ').toLowerCase();
  return haystack.includes(needle);
}

export function TimelineTab({ audit, onSelectNode }: {
  audit: AuditEvent[];
  onSelectNode: (nodeId: string) => void;
}) {
  const [showTechnical, setShowTechnical] = useState(false);
  const [query, setQuery] = useState('');
  const merged = useMemo(() => mergeNodeEvents(audit), [audit]);

  const filteredMerged = merged.filter((row) => rowMatchesQuery(row, query));
  const filteredRaw = audit.filter((event) => (
    [event.node_id, event.event_type, event.actor].join(' ').toLowerCase().includes(query.toLowerCase())
  ));
  const filtered = showTechnical ? filteredRaw : filteredMerged;

  return (
    <div className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search timeline…"
          className="flex-1 min-w-[180px] rounded-md border border-slate-300 px-2.5 py-1.5 text-xs"
        />
        <label className="flex items-center gap-1.5 text-xs text-ink-700 flex-none">
          <input type="checkbox" checked={showTechnical} onChange={(e) => setShowTechnical(e.target.checked)} />
          Show technical events
        </label>
      </div>

      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <div className="grid grid-cols-[110px_120px_1fr_90px_120px_1fr] gap-2 px-3 py-2 bg-slate-50 text-[10px] uppercase tracking-wide text-ink-500 font-medium">
          <span>Time</span>
          <span>Status</span>
          <span>Node</span>
          <span>Duration</span>
          <span>Actor</span>
          <span>Details</span>
        </div>
        <div className="divide-y divide-slate-100">
          {filtered.length === 0 && (
            <div className="px-3 py-3 text-xs text-ink-500">No events match.</div>
          )}
          {!showTechnical && (filtered as TimelineRow[]).map((row, i) => {
            const isFailure = row.kind === 'node' && row.status === 'failed';
            const isHuman = row.kind === 'human';
            const nodeId = rowNodeId(row);
            const duration = row.kind === 'node' && row.startTs
              ? `${((Date.parse(row.endTs) - Date.parse(row.startTs)) / 1000).toFixed(1)}s`
              : '—';
            return (
              <button
                key={i}
                onClick={() => nodeId && onSelectNode(nodeId)}
                disabled={!nodeId}
                className={`w-full grid grid-cols-[110px_120px_1fr_90px_120px_1fr] gap-2 px-3 py-2 text-left text-xs hover:bg-slate-50 disabled:cursor-default ${
                  isFailure ? 'bg-red-50' : isHuman ? 'bg-pink-50' : ''
                }`}
              >
                <span className="font-mono text-[11px] text-ink-500">{clock(rowTs(row))}</span>
                <span className={isFailure ? 'text-bad font-medium' : 'text-ink-700'}>
                  {row.kind === 'node' ? EVENT_TYPE_LABEL[
                    row.status === 'completed' ? 'node_end' : row.status === 'failed' ? 'node_error' : 'node_reused'
                  ] : EVENT_TYPE_LABEL[row.eventType]}
                </span>
                <span className="font-mono text-[11px] text-ink-700 truncate" title={nodeId ?? undefined}>{nodeId ?? '—'}</span>
                <span className="text-ink-500">{duration}</span>
                <span className={row.actor === 'system' ? 'text-ink-500' : 'text-accent-600 font-medium'}>{row.actor}</span>
                <span className="text-ink-500 truncate">{row.reason ?? ''}</span>
              </button>
            );
          })}
          {showTechnical && (filtered as AuditEvent[]).map((event, i) => (
            <button
              key={`${event.node_id}-${event.ts}-${i}`}
              onClick={() => event.node_id !== 'unknown' && onSelectNode(event.node_id)}
              className="w-full grid grid-cols-[110px_120px_1fr_90px_120px_1fr] gap-2 px-3 py-2 text-left text-xs hover:bg-slate-50"
            >
              <span className="font-mono text-[11px] text-ink-500">{clock(event.ts)}</span>
              <span className="text-ink-700">{EVENT_TYPE_LABEL[event.event_type] ?? event.event_type}</span>
              <span className="font-mono text-[11px] text-ink-700 truncate">{event.node_id}</span>
              <span className="text-ink-500">—</span>
              <span className={event.actor === 'system' ? 'text-ink-500' : 'text-accent-600 font-medium'}>{event.actor}</span>
              <span className="text-ink-500 truncate">
                {typeof event.payload === 'object' && event.payload && 'reason' in event.payload
                  ? String((event.payload as Record<string, unknown>).reason)
                  : ''}
              </span>
            </button>
          ))}
        </div>
      </div>
      <p className="mt-3 text-[11px] text-ink-500">
        Audit payloads record shape only — never prompt or proposal content. Records are append-only and scoped to your session.
      </p>
    </div>
  );
}
