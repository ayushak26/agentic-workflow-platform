import { describe, expect, it } from 'vitest';
import type { LibraryMetadata, ReadinessSummary, WorkflowSummary } from '../../../api/types';
import {
  durationBucket,
  emptyFilterState,
  hasActiveFilters,
  matchesFilters,
  matchesSearch,
  sortWorkflows,
} from './filters';

function readiness(level: ReadinessSummary['level']): ReadinessSummary {
  return { level, items: [] };
}

function library(overrides: Partial<LibraryMetadata> = {}): LibraryMetadata {
  return {
    title: 'Title',
    summary: 'Summary',
    purpose: [],
    suitable_for: [],
    not_suitable_for: [],
    outputs: [],
    input_types: [],
    typical_duration: null,
    human_reviews: { count: 0, labels: [] },
    evidence_policy: null,
    visibility_status: 'draft',
    owner_team: null,
    declared: true,
    ...overrides,
  };
}

function workflow(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    name: 'wf',
    description: 'A workflow.',
    use_case: 'generic',
    version: '1.0',
    node_count: 3,
    updated_at: '2026-01-01T00:00:00Z',
    library: library(),
    readiness: readiness('ready'),
    ...overrides,
  };
}

describe('matchesSearch', () => {
  it('matches on description text', () => {
    const wf = workflow({ description: 'Draft a Horizon Part B proposal.' });
    expect(matchesSearch(wf, 'horizon proposal')).toBe(true);
    expect(matchesSearch(wf, 'literature review')).toBe(false);
  });

  it('matches on declared library outputs/purpose', () => {
    const wf = workflow({ library: library({ purpose: ['citation-fact-checking'], outputs: ['pdf'] }) });
    expect(matchesSearch(wf, 'citation')).toBe(true);
    expect(matchesSearch(wf, 'pdf')).toBe(true);
  });

  it('requires every search term to match (AND, not OR)', () => {
    const wf = workflow({ name: 'lead_enrichment_qualification', description: 'Scores leads.' });
    expect(matchesSearch(wf, 'lead score')).toBe(true);
    expect(matchesSearch(wf, 'lead nonexistentterm')).toBe(false);
  });

  it('empty query matches everything', () => {
    expect(matchesSearch(workflow(), '   ')).toBe(true);
  });
});

describe('durationBucket', () => {
  it('buckets by maximum_minutes when present', () => {
    expect(durationBucket(workflow({ library: library({ typical_duration: { minimum_minutes: 5, maximum_minutes: 8 } }) }))).toBe('under-10');
    expect(durationBucket(workflow({ library: library({ typical_duration: { minimum_minutes: 20, maximum_minutes: 25 } }) }))).toBe('10-30');
    expect(durationBucket(workflow({ library: library({ typical_duration: { minimum_minutes: 45, maximum_minutes: 60 } }) }))).toBe('30-60');
    expect(durationBucket(workflow({ library: library({ typical_duration: { minimum_minutes: 90, maximum_minutes: 120 } }) }))).toBe('over-60');
  });

  it('is null when no duration is known', () => {
    expect(durationBucket(workflow())).toBeNull();
  });
});

describe('matchesFilters', () => {
  it('filters by favorites only', () => {
    const wf = workflow({ name: 'a' });
    const filters = { ...emptyFilterState(), favoritesOnly: true };
    expect(matchesFilters(wf, filters, new Set())).toBe(false);
    expect(matchesFilters(wf, filters, new Set(['a']))).toBe(true);
  });

  it('filters by declared output type', () => {
    const wf = workflow({ library: library({ outputs: ['docx'] }) });
    const filters = { ...emptyFilterState(), outputs: new Set(['pdf']) };
    expect(matchesFilters(wf, filters, new Set())).toBe(false);
    expect(matchesFilters(wf, { ...filters, outputs: new Set(['docx']) }, new Set())).toBe(true);
  });

  it('filters by visibility status, defaulting undeclared workflows to draft', () => {
    const wf = workflow({ library: null });
    const filters = { ...emptyFilterState(), statuses: new Set<'draft'>(['draft']) };
    expect(matchesFilters(wf, filters, new Set())).toBe(true);
    expect(matchesFilters(wf, { ...filters, statuses: new Set(['approved']) }, new Set())).toBe(false);
  });

  it('filters by whether human review is required', () => {
    const withReview = workflow({ library: library({ human_reviews: { count: 1, labels: [] } }) });
    const withoutReview = workflow({ library: library({ human_reviews: { count: 0, labels: [] } }) });
    const filters = { ...emptyFilterState(), requiresHumanReview: true };
    expect(matchesFilters(withReview, filters, new Set())).toBe(true);
    expect(matchesFilters(withoutReview, filters, new Set())).toBe(false);
  });

  it('an unset filter category never excludes anything', () => {
    expect(matchesFilters(workflow({ library: null }), emptyFilterState(), new Set())).toBe(true);
  });
});

describe('hasActiveFilters', () => {
  it('is false for the empty state and true once anything is set', () => {
    expect(hasActiveFilters(emptyFilterState())).toBe(false);
    expect(hasActiveFilters({ ...emptyFilterState(), favoritesOnly: true })).toBe(true);
  });
});

describe('sortWorkflows', () => {
  it('sorts by name using the declared title when present', () => {
    const a = workflow({ name: 'b_slug', library: library({ title: 'Alpha' }) });
    const b = workflow({ name: 'a_slug', library: library({ title: 'Beta' }) });
    const sorted = sortWorkflows([b, a], 'name', { recentlyOpened: [] });
    expect(sorted.map(w => w.name)).toEqual(['b_slug', 'a_slug']); // Alpha before Beta
  });

  it('sorts recently-updated newest first', () => {
    const old = workflow({ name: 'old', updated_at: '2025-01-01T00:00:00Z' });
    const fresh = workflow({ name: 'fresh', updated_at: '2026-01-01T00:00:00Z' });
    const sorted = sortWorkflows([old, fresh], 'recently-updated', { recentlyOpened: [] });
    expect(sorted.map(w => w.name)).toEqual(['fresh', 'old']);
  });

  it('sorts shortest-duration first, undeclared duration last', () => {
    const short = workflow({ name: 'short', library: library({ typical_duration: { minimum_minutes: 5, maximum_minutes: 10 } }) });
    const long = workflow({ name: 'long', library: library({ typical_duration: { minimum_minutes: 50, maximum_minutes: 60 } }) });
    const unknown = workflow({ name: 'unknown', library: library({ typical_duration: null }) });
    const sorted = sortWorkflows([unknown, long, short], 'shortest-duration', { recentlyOpened: [] });
    expect(sorted.map(w => w.name)).toEqual(['short', 'long', 'unknown']);
  });

  it('sorts recently-used by recency, unopened workflows last', () => {
    const a = workflow({ name: 'a' });
    const b = workflow({ name: 'b' });
    const c = workflow({ name: 'c' });
    const sorted = sortWorkflows([a, b, c], 'recently-used', { recentlyOpened: ['b', 'a'] });
    expect(sorted.map(w => w.name)).toEqual(['b', 'a', 'c']);
  });

  it('recommended ranks approved+ready workflows above draft+blocked ones', () => {
    const good = workflow({ name: 'good', library: library({ visibility_status: 'approved' }), readiness: readiness('ready') });
    const bad = workflow({ name: 'bad', library: library({ visibility_status: 'draft' }), readiness: readiness('blocked') });
    const sorted = sortWorkflows([bad, good], 'recommended', { recentlyOpened: [] });
    expect(sorted.map(w => w.name)).toEqual(['good', 'bad']);
  });
});
