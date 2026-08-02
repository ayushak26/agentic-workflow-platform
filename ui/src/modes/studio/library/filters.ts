import type { LibraryVisibilityStatus, WorkflowSummary } from '../../../api/types';
import { categoriesForWorkflow, type LibraryCategoryId } from './categories';

export type LibraryDurationBucket = 'under-10' | '10-30' | '30-60' | 'over-60';

export const DURATION_BUCKET_LABEL: Record<LibraryDurationBucket, string> = {
  'under-10': 'Under 10 minutes',
  '10-30': '10–30 minutes',
  '30-60': '30–60 minutes',
  'over-60': 'Over 60 minutes',
};

export type LibraryFilterState = {
  categories: Set<LibraryCategoryId>;
  outputs: Set<string>;
  statuses: Set<LibraryVisibilityStatus>;
  requiresHumanReview: boolean | null;
  durations: Set<LibraryDurationBucket>;
  favoritesOnly: boolean;
};

export function emptyFilterState(): LibraryFilterState {
  return {
    categories: new Set(),
    outputs: new Set(),
    statuses: new Set(),
    requiresHumanReview: null,
    durations: new Set(),
    favoritesOnly: false,
  };
}

export function hasActiveFilters(filters: LibraryFilterState): boolean {
  return (
    filters.categories.size > 0
    || filters.outputs.size > 0
    || filters.statuses.size > 0
    || filters.requiresHumanReview !== null
    || filters.durations.size > 0
    || filters.favoritesOnly
  );
}

export function matchesSearch(workflow: WorkflowSummary, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;
  const haystack = [
    workflow.name,
    workflow.description,
    workflow.use_case,
    workflow.version,
    workflow.library?.title,
    workflow.library?.summary,
    ...(workflow.library?.purpose ?? []),
    ...(workflow.library?.outputs ?? []),
    ...(workflow.library?.input_types ?? []),
  ]
    .filter((value): value is string => Boolean(value))
    .join(' ')
    .toLowerCase();
  return trimmed.split(/\s+/).every(term => haystack.includes(term));
}

export function durationBucket(workflow: WorkflowSummary): LibraryDurationBucket | null {
  const duration = workflow.library?.typical_duration;
  const minutes = duration?.maximum_minutes ?? duration?.minimum_minutes;
  if (minutes == null) return null;
  if (minutes < 10) return 'under-10';
  if (minutes <= 30) return '10-30';
  if (minutes <= 60) return '30-60';
  return 'over-60';
}

export function matchesFilters(
  workflow: WorkflowSummary,
  filters: LibraryFilterState,
  favorites: Set<string>,
): boolean {
  if (filters.favoritesOnly && !favorites.has(workflow.name)) return false;

  if (filters.categories.size > 0) {
    const categories = categoriesForWorkflow(workflow);
    if (!categories.some(category => filters.categories.has(category))) return false;
  }

  if (filters.outputs.size > 0) {
    const outputs = workflow.library?.outputs ?? [];
    if (!outputs.some(output => filters.outputs.has(output))) return false;
  }

  if (filters.statuses.size > 0) {
    const status = workflow.library?.visibility_status ?? 'draft';
    if (!filters.statuses.has(status)) return false;
  }

  if (filters.requiresHumanReview !== null) {
    const hasReview = (workflow.library?.human_reviews.count ?? 0) > 0;
    if (hasReview !== filters.requiresHumanReview) return false;
  }

  if (filters.durations.size > 0) {
    const bucket = durationBucket(workflow);
    if (!bucket || !filters.durations.has(bucket)) return false;
  }

  return true;
}

export type LibrarySortKey =
  | 'recommended'
  | 'name'
  | 'recently-updated'
  | 'shortest-duration'
  | 'recently-used';

export const SORT_LABEL: Record<LibrarySortKey, string> = {
  recommended: 'Recommended',
  name: 'Name',
  'recently-updated': 'Recently updated',
  'shortest-duration': 'Shortest duration',
  'recently-used': 'Recently used',
};

function durationMinutesForSort(workflow: WorkflowSummary): number {
  const duration = workflow.library?.typical_duration;
  const minutes = duration?.minimum_minutes ?? duration?.maximum_minutes;
  return minutes ?? Number.POSITIVE_INFINITY;
}

function recommendationScore(workflow: WorkflowSummary): number {
  let score = 0;
  if (workflow.readiness.level === 'ready') score += 3;
  else if (workflow.readiness.level === 'ready_with_warnings') score += 1;
  if (workflow.library?.visibility_status === 'approved') score += 2;
  return score;
}

export function sortWorkflows(
  list: WorkflowSummary[],
  sortKey: LibrarySortKey,
  context: { recentlyOpened: string[] },
): WorkflowSummary[] {
  const sorted = [...list];
  switch (sortKey) {
    case 'name':
      sorted.sort((a, b) => (
        (a.library?.title ?? a.name).localeCompare(b.library?.title ?? b.name)
      ));
      break;
    case 'recently-updated':
      sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      break;
    case 'shortest-duration':
      sorted.sort((a, b) => durationMinutesForSort(a) - durationMinutesForSort(b));
      break;
    case 'recently-used': {
      const rank = new Map(context.recentlyOpened.map((name, index) => [name, index]));
      sorted.sort((a, b) => (
        (rank.get(a.name) ?? Number.POSITIVE_INFINITY) - (rank.get(b.name) ?? Number.POSITIVE_INFINITY)
      ));
      break;
    }
    case 'recommended':
    default:
      sorted.sort((a, b) => recommendationScore(b) - recommendationScore(a));
      break;
  }
  return sorted;
}
