import { describe, expect, it } from 'vitest';
import {
  applyCancellation,
  computePathHighlight,
  computeReachability,
  computeStatusCounts,
  deriveCockpitState,
  type NodeStatus,
} from './cockpit-state';

describe('deriveCockpitState', () => {
  it('marks a node active on node_started and done on node_completed', () => {
    const state = deriveCockpitState(
      ['a', 'b'],
      [
        { type: 'node_started', run_id: 'r', node_id: 'a', ts: 't1' },
        { type: 'node_completed', run_id: 'r', node_id: 'a', output_preview: 'ok', ts: 't2' },
      ],
      true,
    );
    expect(state.nodeStates.a).toBe('done');
    expect(state.nodeStates.b).toBe('pending');
    expect(state.outputPreviews.a).toBe('ok');
  });

  it('sets runStatus to failed and marks the failing node', () => {
    const state = deriveCockpitState(
      ['a'],
      [{ type: 'run_failed', run_id: 'r', node_id: 'a', error: 'boom', ts: 't1' }],
      true,
    );
    expect(state.runStatus).toBe('failed');
    expect(state.nodeStates.a).toBe('failed');
    expect(state.errorMessage).toBe('boom');
  });
});

describe('computeReachability', () => {
  const nodes = [{ id: 'start' }, { id: 'left' }, { id: 'right' }, { id: 'join' }];

  it('leaves a plain linear chain alone', () => {
    const edges = [
      { source: 'start', target: 'left' },
      { source: 'left', target: 'join' },
    ];
    const states: Record<string, NodeStatus> = { start: 'done', left: 'pending', join: 'pending' };
    const result = computeReachability(nodes, edges, states, {});
    expect(result.left).toBe('pending');
    expect(result.join).toBe('pending');
  });

  it('marks the untaken branch of a decided router as skipped', () => {
    const edges = [
      { source: 'start', target: 'left', label: 'left' },
      { source: 'start', target: 'right', label: 'right' },
      { source: 'left', target: 'join' },
      { source: 'right', target: 'join' },
    ];
    const states: Record<string, NodeStatus> = {
      start: 'done', left: 'pending', right: 'pending', join: 'pending',
    };
    const outputs = { start: { route: 'left' } };
    const result = computeReachability(nodes, edges, states, outputs);
    expect(result.left).toBe('pending');
    expect(result.right).toBe('skipped');
    // join is still reachable via `left`, so it must NOT be skipped even
    // though one of its two incoming edges is dead.
    expect(result.join).toBe('pending');
  });

  it('does not skip a node whose router has not decided yet', () => {
    const edges = [
      { source: 'start', target: 'left', label: 'left' },
      { source: 'start', target: 'right', label: 'right' },
    ];
    const states: Record<string, NodeStatus> = { start: 'active', left: 'pending', right: 'pending' };
    const result = computeReachability(nodes, edges, states, {});
    expect(result.left).toBe('pending');
    expect(result.right).toBe('pending');
  });

  it('propagates skipped status downstream of a skipped node', () => {
    const chain = [{ id: 'start' }, { id: 'a' }, { id: 'b' }, { id: 'c' }];
    const edges = [
      { source: 'start', target: 'a', label: 'taken' },
      { source: 'start', target: 'b', label: 'untaken' },
      { source: 'b', target: 'c' },
    ];
    const states: Record<string, NodeStatus> = {
      start: 'done', a: 'pending', b: 'pending', c: 'pending',
    };
    const outputs = { start: { route: 'taken' } };
    const result = computeReachability(chain, edges, states, outputs);
    expect(result.b).toBe('skipped');
    expect(result.c).toBe('skipped');
  });
});

describe('applyCancellation', () => {
  it('leaves nodes untouched while the run is still going', () => {
    const states: Record<string, NodeStatus> = { a: 'pending', b: 'done' };
    expect(applyCancellation(states, false)).toEqual(states);
  });

  it('relabels only still-pending nodes once the run has ended', () => {
    const states: Record<string, NodeStatus> = { a: 'pending', b: 'done', c: 'skipped' };
    const result = applyCancellation(states, true);
    expect(result.a).toBe('cancelled');
    expect(result.b).toBe('done');
    expect(result.c).toBe('skipped');
  });
});

describe('computeStatusCounts', () => {
  it('tallies each bucket correctly', () => {
    const states: Record<string, NodeStatus> = {
      a: 'done', b: 'reused', c: 'active', d: 'pending', e: 'paused', f: 'failed', g: 'skipped', h: 'cancelled',
    };
    expect(computeStatusCounts(states)).toEqual({
      total: 8, completed: 2, running: 1, waiting: 1, paused: 1, failed: 1, skipped: 1, cancelled: 1,
    });
  });
});

describe('computePathHighlight', () => {
  const edges = [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'x', target: 'y' },
  ];

  it('returns an empty set when nothing is selected', () => {
    expect(computePathHighlight(null, edges).size).toBe(0);
  });

  it('includes upstream and downstream nodes but not unrelated branches', () => {
    const highlighted = computePathHighlight('b', edges);
    expect(highlighted.has('a')).toBe(true);
    expect(highlighted.has('b')).toBe(true);
    expect(highlighted.has('c')).toBe(true);
    expect(highlighted.has('x')).toBe(false);
    expect(highlighted.has('y')).toBe(false);
  });
});
