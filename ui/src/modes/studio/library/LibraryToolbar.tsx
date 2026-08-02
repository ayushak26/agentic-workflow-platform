import { useState } from 'react';
import type { LibraryVisibilityStatus } from '../../../api/types';
import { Icon } from '../../../components/ui/Icon';
import {
  DURATION_BUCKET_LABEL,
  SORT_LABEL,
  hasActiveFilters,
  type LibraryDurationBucket,
  type LibraryFilterState,
  type LibrarySortKey,
} from './filters';

export type LibraryViewMode = 'grid' | 'list';

const STATUS_OPTIONS: LibraryVisibilityStatus[] = ['approved', 'draft', 'in_review', 'deprecated', 'archived'];
const STATUS_LABEL: Record<LibraryVisibilityStatus, string> = {
  approved: 'Approved',
  draft: 'Draft',
  in_review: 'In review',
  deprecated: 'Deprecated',
  archived: 'Archived',
};
const DURATION_OPTIONS: LibraryDurationBucket[] = ['under-10', '10-30', '30-60', 'over-60'];

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value); else next.add(value);
  return next;
}

export function LibraryToolbar({
  search,
  onSearchChange,
  sortKey,
  onSortKeyChange,
  filters,
  onFiltersChange,
  viewMode,
  onViewModeChange,
  onRefresh,
  refreshing,
  availableOutputs,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  sortKey: LibrarySortKey;
  onSortKeyChange: (value: LibrarySortKey) => void;
  filters: LibraryFilterState;
  onFiltersChange: (next: LibraryFilterState) => void;
  viewMode: LibraryViewMode;
  onViewModeChange: (mode: LibraryViewMode) => void;
  onRefresh: () => void;
  refreshing: boolean;
  availableOutputs: string[];
}) {
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const active = hasActiveFilters(filters);

  return (
    <div className="library-toolbar">
      <div className="library-search">
        <Icon name="search" size={15} />
        <input
          aria-label="Search workflows"
          value={search}
          onChange={event => onSearchChange(event.target.value)}
          placeholder="Search by outcome, output, or purpose…"
          type="search"
        />
      </div>

      <div className="library-toolbar-controls">
        <div className="library-filter-anchor">
          <button
            type="button"
            className={`ui-button ui-button--secondary ${active ? 'library-filter-active' : ''}`}
            aria-expanded={filterPanelOpen}
            onClick={() => setFilterPanelOpen(value => !value)}
          >
            <Icon name="filter" size={14} /> Filters {active ? `(${
              filters.outputs.size + filters.statuses.size + filters.durations.size
              + (filters.requiresHumanReview !== null ? 1 : 0) + (filters.favoritesOnly ? 1 : 0)
            })` : ''}
          </button>
          {filterPanelOpen && (
            <div className="library-filter-panel" role="dialog" aria-label="Filter workflows">
              <section>
                <h4>Output</h4>
                <div className="library-filter-options">
                  {availableOutputs.map(output => (
                    <label key={output}>
                      <input
                        type="checkbox"
                        checked={filters.outputs.has(output)}
                        onChange={() => onFiltersChange({ ...filters, outputs: toggleInSet(filters.outputs, output) })}
                      />
                      {output.toUpperCase()}
                    </label>
                  ))}
                  {availableOutputs.length === 0 && <span className="library-filter-empty">No known outputs yet.</span>}
                </div>
              </section>
              <section>
                <h4>Status</h4>
                <div className="library-filter-options">
                  {STATUS_OPTIONS.map(status => (
                    <label key={status}>
                      <input
                        type="checkbox"
                        checked={filters.statuses.has(status)}
                        onChange={() => onFiltersChange({ ...filters, statuses: toggleInSet(filters.statuses, status) })}
                      />
                      {STATUS_LABEL[status]}
                    </label>
                  ))}
                </div>
              </section>
              <section>
                <h4>Human review</h4>
                <div className="library-filter-options">
                  <label>
                    <input
                      type="checkbox"
                      checked={filters.requiresHumanReview === true}
                      onChange={() => onFiltersChange({
                        ...filters,
                        requiresHumanReview: filters.requiresHumanReview === true ? null : true,
                      })}
                    />
                    Requires human review
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={filters.requiresHumanReview === false}
                      onChange={() => onFiltersChange({
                        ...filters,
                        requiresHumanReview: filters.requiresHumanReview === false ? null : false,
                      })}
                    />
                    Fully independent
                  </label>
                </div>
              </section>
              <section>
                <h4>Duration</h4>
                <div className="library-filter-options">
                  {DURATION_OPTIONS.map(bucket => (
                    <label key={bucket}>
                      <input
                        type="checkbox"
                        checked={filters.durations.has(bucket)}
                        onChange={() => onFiltersChange({ ...filters, durations: toggleInSet(filters.durations, bucket) })}
                      />
                      {DURATION_BUCKET_LABEL[bucket]}
                    </label>
                  ))}
                </div>
              </section>
              <div className="library-filter-footer">
                <button
                  type="button"
                  className="ui-button ui-button--secondary"
                  disabled={!active}
                  onClick={() => onFiltersChange({
                    categories: filters.categories,
                    outputs: new Set(),
                    statuses: new Set(),
                    requiresHumanReview: null,
                    durations: new Set(),
                    favoritesOnly: false,
                  })}
                >
                  Reset all
                </button>
                <button type="button" className="ui-button ui-button--primary" onClick={() => setFilterPanelOpen(false)}>
                  Done
                </button>
              </div>
            </div>
          )}
        </div>

        <label className="library-sort">
          <span>Sort</span>
          <select value={sortKey} onChange={event => onSortKeyChange(event.target.value as LibrarySortKey)}>
            {(Object.keys(SORT_LABEL) as LibrarySortKey[]).map(key => (
              <option key={key} value={key}>{SORT_LABEL[key]}</option>
            ))}
          </select>
        </label>

        <div className="library-view-toggle" role="radiogroup" aria-label="Layout">
          <button
            type="button"
            role="radio"
            aria-checked={viewMode === 'grid'}
            className={viewMode === 'grid' ? 'is-active' : ''}
            onClick={() => onViewModeChange('grid')}
            title="Grid view"
          >
            <Icon name="grid" size={14} />
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={viewMode === 'list'}
            className={viewMode === 'list' ? 'is-active' : ''}
            onClick={() => onViewModeChange('list')}
            title="List view"
          >
            <Icon name="rows" size={14} />
          </button>
        </div>

        <button
          type="button"
          className="ui-icon-button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh workflow list"
          title="Refresh"
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>
    </div>
  );
}
