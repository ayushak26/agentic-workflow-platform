import { describe, expect, it } from 'vitest';
import type { RunSummary } from '../../../api/types';
import {
  filterRuns, groupRuns, matchesSearch, relativeTime, sortRuns,
} from './run-list-utils';

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: 'r1',
    session_id: 's1',
    workflow_name: 'Horizon Evidence',
    status: 'completed',
    started_at: 1_700_000_000,
    ended_at: 1_700_000_100,
    duration_s: 100,
    node_count: 10,
    completed_node_count: 10,
    active_nodes: [],
    error: null,
    created_at: new Date(1_700_000_000 * 1000).toISOString(),
    updated_at: new Date(1_700_000_100 * 1000).toISOString(),
    ...overrides,
  };
}

describe('matchesSearch', () => {
  const run = makeRun({
    workflow_name: 'Horizon Europe Part B — Evidence',
    run_id: 'abc-123',
    stage_id: 'evidence',
    pipeline_name: 'Horizon Staged Pipeline',
  });

  it('matches on workflow name, run id, stage id, and pipeline name (case-insensitive)', () => {
    expect(matchesSearch(run, 'evidence')).toBe(true);
    expect(matchesSearch(run, 'ABC-123')).toBe(true);
    expect(matchesSearch(run, 'staged pipeline')).toBe(true);
    expect(matchesSearch(run, 'nonexistent')).toBe(false);
  });

  it('treats an empty/blank query as matching everything', () => {
    expect(matchesSearch(run, '')).toBe(true);
    expect(matchesSearch(run, '   ')).toBe(true);
  });
});

describe('filterRuns', () => {
  const runs = [
    makeRun({ run_id: 'a', status: 'completed', completed_node_count: 5 }),
    makeRun({ run_id: 'b', status: 'failed', completed_node_count: 0, error: 'boom', failed_node: 'n1' }),
    makeRun({ run_id: 'c', status: 'running', completed_node_count: 2 }),
  ];

  it('filters by status set', () => {
    const result = filterRuns(runs, { statuses: new Set(['failed']) });
    expect(result.map((r) => r.run_id)).toEqual(['b']);
  });

  it('filters by hasErrors', () => {
    const result = filterRuns(runs, { hasErrors: true });
    expect(result.map((r) => r.run_id)).toEqual(['b']);
  });

  it('filters by hasOutputs (proxy: completed_node_count > 0)', () => {
    const result = filterRuns(runs, { hasOutputs: true });
    expect(result.map((r) => r.run_id).sort()).toEqual(['a', 'c']);
  });

  it('combines a query filter with a status filter', () => {
    const result = filterRuns(runs, { query: 'n1', statuses: new Set(['failed', 'running']) });
    expect(result.map((r) => r.run_id)).toEqual(['b']);
  });
});

describe('sortRuns', () => {
  const runs = [
    makeRun({ run_id: 'old', started_at: 1000, workflow_name: 'Zeta' }),
    makeRun({ run_id: 'new', started_at: 3000, workflow_name: 'Alpha' }),
    makeRun({ run_id: 'mid', started_at: 2000, status: 'failed', workflow_name: 'Mid' }),
  ];

  it('sorts recent-first and oldest-first', () => {
    expect(sortRuns(runs, 'recent').map((r) => r.run_id)).toEqual(['new', 'mid', 'old']);
    expect(sortRuns(runs, 'oldest').map((r) => r.run_id)).toEqual(['old', 'mid', 'new']);
  });

  it('puts failed runs first without discarding recency as the tiebreaker', () => {
    expect(sortRuns(runs, 'failed-first').map((r) => r.run_id)).toEqual(['mid', 'new', 'old']);
  });

  it('sorts by workflow name alphabetically', () => {
    expect(sortRuns(runs, 'workflow-name').map((r) => r.workflow_name)).toEqual(['Alpha', 'Mid', 'Zeta']);
  });

  it('does not mutate the input array', () => {
    const copy = [...runs];
    sortRuns(runs, 'recent');
    expect(runs).toEqual(copy);
  });
});

describe('groupRuns', () => {
  it('groups by date bucket relative to `now`', () => {
    const now = 1_700_100_000 * 1000; // fixed instant for deterministic grouping
    const runs = [
      makeRun({ run_id: 'today', started_at: 1_700_099_000 }),
      makeRun({ run_id: 'yesterday', started_at: 1_700_100_000 - 90_000 }),
      makeRun({ run_id: 'older', started_at: 1_699_000_000 }),
    ];
    const groups = groupRuns(runs, 'date', now);
    expect(groups.map((g) => g.label)).toEqual(['Today', 'Yesterday', 'Older']);
  });

  it('groups by workflow name', () => {
    const runs = [
      makeRun({ run_id: 'a', workflow_name: 'X' }),
      makeRun({ run_id: 'b', workflow_name: 'Y' }),
      makeRun({ run_id: 'c', workflow_name: 'X' }),
    ];
    const groups = groupRuns(runs, 'workflow');
    expect(groups.find((g) => g.label === 'X')?.runs.map((r) => r.run_id)).toEqual(['a', 'c']);
  });

  it('returns a single "All runs" group for "none", and nothing for an empty list', () => {
    expect(groupRuns([makeRun()], 'none')).toHaveLength(1);
    expect(groupRuns([], 'none')).toHaveLength(0);
  });
});

describe('relativeTime', () => {
  const now = 1_700_000_000 * 1000;

  it('formats minutes/hours/days ago', () => {
    expect(relativeTime(1_700_000_000 - 60, now)).toBe('1m ago');
    expect(relativeTime(1_700_000_000 - 3600, now)).toBe('1h ago');
    expect(relativeTime(1_700_000_000 - 86400, now)).toBe('1d ago');
  });

  it('returns "just now" for very recent timestamps and "—" for null', () => {
    expect(relativeTime(1_700_000_000 - 5, now)).toBe('just now');
    expect(relativeTime(null, now)).toBe('—');
  });
});
