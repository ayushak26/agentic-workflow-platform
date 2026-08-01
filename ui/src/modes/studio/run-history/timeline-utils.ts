import type { AuditEvent } from '../../../api/types';

/**
 * One coherent row per node execution (start+end merged) or human decision,
 * instead of the raw audit feed's separate "Node started"/"Node completed"
 * rows. There is no run-level lifecycle audit event today (no
 * run_started/run_paused/run_resumed/run_completed entries in `AuditEvent`
 * — only node_start/node_end/node_reused/node_error/hitl_*) — the run's own
 * overall timing/status is already shown in the Run Workspace header, so
 * this view only needs to cover the per-node + human-decision trail.
 */
export type TimelineRow =
  | {
      kind: 'node';
      nodeId: string;
      status: 'completed' | 'failed' | 'reused';
      startTs: string | null;
      endTs: string;
      actor: string;
      reason?: string;
    }
  | {
      kind: 'human';
      nodeId: string | null;
      eventType: 'hitl_approve' | 'hitl_reject' | 'hitl_edit';
      ts: string;
      actor: string;
      reason?: string;
    };

const END_EVENT_TYPES = new Set(['node_end', 'node_reused', 'node_error']);

function nodeStatusForEventType(eventType: string): 'completed' | 'failed' | 'reused' {
  if (eventType === 'node_error') return 'failed';
  if (eventType === 'node_reused') return 'reused';
  return 'completed';
}

/**
 * Merges each node_start with its corresponding node_end/node_reused/
 * node_error (matched by node_id, in chronological order — the first
 * unmatched start for that node pairs with the first end that follows it),
 * leaves hitl_* events as their own rows, and drops nothing: a start with
 * no matching end yet (run still in progress) is kept as its own row so
 * "currently running" is still visible.
 */
export function mergeNodeEvents(events: AuditEvent[]): TimelineRow[] {
  const ordered = [...events].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
  const pendingStarts = new Map<string, AuditEvent[]>();
  const rows: TimelineRow[] = [];

  for (const event of ordered) {
    if (event.event_type === 'node_start') {
      const list = pendingStarts.get(event.node_id) ?? [];
      list.push(event);
      pendingStarts.set(event.node_id, list);
      continue;
    }
    if (END_EVENT_TYPES.has(event.event_type)) {
      const list = pendingStarts.get(event.node_id);
      const start = list?.shift();
      const reason = (event.payload as Record<string, unknown> | null)?.reason as string | undefined;
      rows.push({
        kind: 'node',
        nodeId: event.node_id,
        status: nodeStatusForEventType(event.event_type),
        startTs: start?.ts ?? null,
        endTs: event.ts,
        actor: event.actor,
        reason,
      });
      continue;
    }
    if (event.event_type === 'hitl_approve' || event.event_type === 'hitl_reject' || event.event_type === 'hitl_edit') {
      const reason = (event.payload as Record<string, unknown> | null)?.reason as string | undefined;
      rows.push({
        kind: 'human',
        nodeId: event.node_id !== 'unknown' ? event.node_id : null,
        eventType: event.event_type,
        ts: event.ts,
        actor: event.actor,
        reason,
      });
    }
  }

  // Any node_start left with no matching end yet — the node is still
  // running (or the run ended abruptly before it finished).
  for (const [nodeId, starts] of pendingStarts) {
    for (const start of starts) {
      rows.push({
        kind: 'node', nodeId, status: 'completed', startTs: start.ts, endTs: start.ts, actor: start.actor,
      });
    }
  }

  return rows.sort((a, b) => {
    const aTs = a.kind === 'node' ? a.endTs : a.ts;
    const bTs = b.kind === 'node' ? b.endTs : b.ts;
    return Date.parse(aTs) - Date.parse(bTs);
  });
}
