import { describe, expect, it } from 'vitest';
import type { AuditEvent } from '../../../api/types';
import { mergeNodeEvents } from './timeline-utils';

function ev(overrides: Partial<AuditEvent>): AuditEvent {
  return {
    run_id: 'r1',
    session_id: 's1',
    node_id: 'n1',
    event_type: 'node_start',
    actor: 'system',
    payload: {},
    ts: '2024-01-01T00:00:00.000Z',
    ...overrides,
  };
}

describe('mergeNodeEvents', () => {
  it('merges a node_start + node_end pair into one completed row', () => {
    const rows = mergeNodeEvents([
      ev({ event_type: 'node_start', ts: '2024-01-01T00:00:00.000Z' }),
      ev({ event_type: 'node_end', ts: '2024-01-01T00:00:05.000Z' }),
    ]);
    expect(rows).toEqual([{
      kind: 'node',
      nodeId: 'n1',
      status: 'completed',
      startTs: '2024-01-01T00:00:00.000Z',
      endTs: '2024-01-01T00:00:05.000Z',
      actor: 'system',
      reason: undefined,
    }]);
  });

  it('marks a node_error pair as failed', () => {
    const rows = mergeNodeEvents([
      ev({ event_type: 'node_start', ts: '2024-01-01T00:00:00.000Z' }),
      ev({ event_type: 'node_error', ts: '2024-01-01T00:00:01.000Z' }),
    ]);
    expect(rows[0]).toMatchObject({ kind: 'node', status: 'failed' });
  });

  it('keeps hitl decisions as their own distinct rows, not merged into node rows', () => {
    const rows = mergeNodeEvents([
      ev({ event_type: 'node_start', node_id: 'review', ts: '2024-01-01T00:00:00.000Z' }),
      ev({
        event_type: 'hitl_approve', node_id: 'review', actor: 'ayush', ts: '2024-01-01T00:00:02.000Z',
      }),
      ev({ event_type: 'node_end', node_id: 'review', ts: '2024-01-01T00:00:03.000Z' }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(['human', 'node']);
    expect(rows[0]).toMatchObject({ kind: 'human', eventType: 'hitl_approve', actor: 'ayush' });
  });

  it('keeps a node_start with no matching end yet as its own row (still running)', () => {
    const rows = mergeNodeEvents([ev({ event_type: 'node_start' })]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: 'node', nodeId: 'n1' });
  });

  it('pairs multiple starts/ends for the same node id in chronological order (a retried node)', () => {
    const rows = mergeNodeEvents([
      ev({ event_type: 'node_start', ts: '2024-01-01T00:00:00.000Z' }),
      ev({ event_type: 'node_error', ts: '2024-01-01T00:00:01.000Z' }),
      ev({ event_type: 'node_start', ts: '2024-01-01T00:00:02.000Z' }),
      ev({ event_type: 'node_end', ts: '2024-01-01T00:00:03.000Z' }),
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ status: 'failed', startTs: '2024-01-01T00:00:00.000Z' });
    expect(rows[1]).toMatchObject({ status: 'completed', startTs: '2024-01-01T00:00:02.000Z' });
  });

  it('sorts output rows chronologically regardless of input order', () => {
    const rows = mergeNodeEvents([
      ev({
        node_id: 'b', event_type: 'node_start', ts: '2024-01-01T00:00:05.000Z',
      }),
      ev({
        node_id: 'b', event_type: 'node_end', ts: '2024-01-01T00:00:06.000Z',
      }),
      ev({
        node_id: 'a', event_type: 'node_start', ts: '2024-01-01T00:00:00.000Z',
      }),
      ev({
        node_id: 'a', event_type: 'node_end', ts: '2024-01-01T00:00:01.000Z',
      }),
    ]);
    expect(rows.map((r) => (r.kind === 'node' ? r.nodeId : null))).toEqual(['a', 'b']);
  });
});
