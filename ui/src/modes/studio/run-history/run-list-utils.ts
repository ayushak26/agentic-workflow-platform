import type { RunStatus, RunSummary } from '../../../api/types';

/** Display label for a RunStatus — distinct from cockpit-state's NodeStatus labels. */
export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  running: 'Running',
  paused: 'Paused',
  completed: 'Successful',
  rejected: 'Rejected',
  failed: 'Failed',
};

export type SortKey =
  | 'recent'
  | 'oldest'
  | 'failed-first'
  | 'running-first'
  | 'duration'
  | 'workflow-name';

export type GroupBy = 'date' | 'workflow' | 'status' | 'none';

export type RunFilters = {
  statuses?: ReadonlySet<RunStatus>;
  dateFrom?: number; // epoch seconds, inclusive
  dateTo?: number; // epoch seconds, inclusive
  workflowName?: string;
  // RunSummary has no direct "has outputs" flag — completed_node_count > 0
  // is used as a proxy (documented approximation: a completed node usually,
  // but not always, produces output).
  hasOutputs?: boolean;
  hasErrors?: boolean;
  query?: string;
};

function runTimestamp(run: RunSummary): number | null {
  return run.started_at ?? (run.created_at ? Date.parse(run.created_at) / 1000 : null);
}

/** Case-insensitive substring match against everything a user would plausibly search by. */
export function matchesSearch(run: RunSummary, query: string): boolean {
  if (!query.trim()) return true;
  const needle = query.trim().toLowerCase();
  const haystack = [
    run.workflow_name,
    run.run_id,
    run.status,
    run.failed_node,
  ]
    .filter((value): value is string => Boolean(value))
    .join(' ')
    .toLowerCase();
  return haystack.includes(needle);
}

function runHasErrors(run: RunSummary): boolean {
  return run.status === 'failed' || Boolean(run.error) || Boolean(run.failed_node);
}

export function filterRuns(runs: RunSummary[], filters: RunFilters): RunSummary[] {
  return runs.filter((run) => {
    if (filters.statuses && filters.statuses.size > 0 && !filters.statuses.has(run.status)) {
      return false;
    }
    const ts = runTimestamp(run);
    if (filters.dateFrom != null && (ts == null || ts < filters.dateFrom)) return false;
    if (filters.dateTo != null && (ts == null || ts > filters.dateTo)) return false;
    if (filters.workflowName && run.workflow_name !== filters.workflowName) return false;
    if (filters.hasOutputs && (run.completed_node_count ?? 0) === 0) return false;
    if (filters.hasErrors && !runHasErrors(run)) return false;
    if (filters.query && !matchesSearch(run, filters.query)) return false;
    return true;
  });
}

export function sortRuns(runs: RunSummary[], key: SortKey): RunSummary[] {
  const sorted = [...runs];
  switch (key) {
    case 'recent':
      return sorted.sort((a, b) => (runTimestamp(b) ?? 0) - (runTimestamp(a) ?? 0));
    case 'oldest':
      return sorted.sort((a, b) => (runTimestamp(a) ?? 0) - (runTimestamp(b) ?? 0));
    case 'failed-first':
      return sorted.sort((a, b) => (
        (a.status === 'failed' ? 0 : 1) - (b.status === 'failed' ? 0 : 1)
        || (runTimestamp(b) ?? 0) - (runTimestamp(a) ?? 0)
      ));
    case 'running-first':
      return sorted.sort((a, b) => (
        (a.status === 'running' ? 0 : 1) - (b.status === 'running' ? 0 : 1)
        || (runTimestamp(b) ?? 0) - (runTimestamp(a) ?? 0)
      ));
    case 'duration':
      return sorted.sort((a, b) => (b.duration_s ?? -1) - (a.duration_s ?? -1));
    case 'workflow-name':
      return sorted.sort((a, b) => a.workflow_name.localeCompare(b.workflow_name));
    default:
      return sorted;
  }
}

export type RunGroup = { key: string; label: string; runs: RunSummary[] };

const DAY_MS = 24 * 60 * 60 * 1000;

function dateGroupLabel(run: RunSummary, now: number): string {
  const ts = runTimestamp(run);
  if (ts == null) return 'Unknown date';
  const ageMs = now - ts * 1000;
  if (ageMs < 0) return 'Today';
  const ageDays = Math.floor(ageMs / DAY_MS);
  if (ageDays === 0) return 'Today';
  if (ageDays === 1) return 'Yesterday';
  if (ageDays < 8) return 'Previous 7 days';
  return 'Older';
}

const DATE_GROUP_ORDER = ['Today', 'Yesterday', 'Previous 7 days', 'Older', 'Unknown date'];

export function groupRuns(runs: RunSummary[], by: GroupBy, now: number = Date.now()): RunGroup[] {
  if (by === 'none') {
    return runs.length > 0 ? [{ key: 'all', label: 'All runs', runs }] : [];
  }
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    const key = by === 'date' ? dateGroupLabel(run, now)
      : by === 'workflow' ? run.workflow_name
      : run.status;
    const list = groups.get(key) ?? [];
    list.push(run);
    groups.set(key, list);
  }
  const keys = by === 'date'
    ? DATE_GROUP_ORDER.filter((key) => groups.has(key))
    : Array.from(groups.keys()).sort();
  return keys.map((key) => ({ key, label: key, runs: groups.get(key)! }));
}

/** "just now" / "5m ago" / "3h ago" / "2d ago" — epoch seconds in, relative-to-now string out. */
export function relativeTime(epochSeconds: number | null, now: number = Date.now()): string {
  if (epochSeconds == null) return '—';
  const ageMs = now - epochSeconds * 1000;
  if (ageMs < 0 || ageMs < 30_000) return 'just now';
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
