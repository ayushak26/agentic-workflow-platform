import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { WorkflowSummary } from '../../api/types';
import { Icon } from '../../components/ui/Icon';
import { Spinner } from '../../components/Spinner';
import { GenerateWorkflowDialog } from './GenerateWorkflowDialog';
import { CategoryNav, type LibraryNavSelection } from './library/CategoryNav';
import { categoriesForWorkflow, type LibraryCategoryId } from './library/categories';
import { ConfirmDeleteDialog } from './library/ConfirmDeleteDialog';
import {
  emptyFilterState,
  matchesFilters,
  matchesSearch,
  sortWorkflows,
  type LibraryFilterState,
  type LibrarySortKey,
} from './library/filters';
import { ImportWorkflowDialog } from './library/ImportWorkflowDialog';
import { forgetWorkflow, getFavorites, getRecentlyOpened, recordOpened, toggleFavorite } from './library/localState';
import { PrepareAndRunPanel } from './library/PrepareAndRunPanel';
import { WorkflowCard } from './library/WorkflowCard';
import { LibraryToolbar, type LibraryViewMode } from './library/LibraryToolbar';
import { WorkflowDetailsPanel } from './library/WorkflowDetailsPanel';

export function Library() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showGenerate, setShowGenerate] = useState(false);
  const [showImport, setShowImport] = useState(false);

  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<LibrarySortKey>('recommended');
  const [filters, setFilters] = useState<LibraryFilterState>(emptyFilterState());
  const [navSelection, setNavSelection] = useState<LibraryNavSelection>('all');
  const [viewMode, setViewMode] = useState<LibraryViewMode>('grid');
  const [categoryNavOpen, setCategoryNavOpen] = useState(false);

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [prepareRunTarget, setPrepareRunTarget] = useState<WorkflowSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkflowSummary | null>(null);
  const [favorites, setFavorites] = useState<Set<string>>(() => getFavorites());
  const [recentlyOpened, setRecentlyOpened] = useState<string[]>(() => getRecentlyOpened());

  function loadWorkflows() {
    setRefreshing(true);
    return api.listWorkflows()
      .then(list => { setWorkflows(list); setError(null); })
      .catch(reason => setError(String(reason)))
      .finally(() => setRefreshing(false));
  }

  useEffect(() => {
    // One-time mount fetch, not a sync-to-external-system loop — same
    // justification as the analogous effects in Builder.tsx/Cockpit.tsx.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadWorkflows();
  }, []);

  const categoryCounts = useMemo(() => {
    const counts: Partial<Record<LibraryCategoryId, number>> = {};
    for (const workflow of workflows ?? []) {
      for (const category of categoriesForWorkflow(workflow)) {
        counts[category] = (counts[category] ?? 0) + 1;
      }
    }
    return counts;
  }, [workflows]);

  const availableOutputs = useMemo(() => {
    const outputs = new Set<string>();
    for (const workflow of workflows ?? []) {
      for (const output of workflow.library?.outputs ?? []) outputs.add(output);
    }
    return [...outputs].sort();
  }, [workflows]);

  const visible = useMemo(() => {
    if (!workflows) return [];
    const byNav = workflows.filter(workflow => {
      if (navSelection === 'all') return true;
      if (navSelection === 'favorites') return favorites.has(workflow.name);
      return categoriesForWorkflow(workflow).includes(navSelection);
    });
    const bySearchAndFilters = byNav.filter(workflow => (
      matchesSearch(workflow, search) && matchesFilters(workflow, filters, favorites)
    ));
    return sortWorkflows(bySearchAndFilters, sortKey, { recentlyOpened });
  }, [workflows, navSelection, favorites, search, filters, sortKey, recentlyOpened]);

  const lastOpened = recentlyOpened[0]
    ? (workflows ?? []).find(workflow => workflow.name === recentlyOpened[0])
    : undefined;
  const recentOthers = recentlyOpened
    .slice(1, 5)
    .map(name => (workflows ?? []).find(workflow => workflow.name === name))
    .filter((workflow): workflow is WorkflowSummary => Boolean(workflow));
  const approvedFeatured = (workflows ?? [])
    .filter(workflow => workflow.library?.visibility_status === 'approved')
    .slice(0, 4);
  const showFeatured = navSelection === 'all' && !search && recentlyOpened.length === 0 && approvedFeatured.length > 0;

  function selectWorkflow(name: string) {
    setSelectedName(name);
    setRecentlyOpened(recordOpened(name));
  }

  function toggleFavoriteFor(name: string) {
    setFavorites(toggleFavorite(name));
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const { name } = deleteTarget;
    await api.deleteWorkflow(name);
    forgetWorkflow(name);
    setWorkflows(current => (current ?? []).filter(workflow => workflow.name !== name));
    setFavorites(getFavorites());
    setRecentlyOpened(getRecentlyOpened());
    if (selectedName === name) setSelectedName(null);
    if (prepareRunTarget?.name === name) setPrepareRunTarget(null);
    setDeleteTarget(null);
  }

  const selectedWorkflow = selectedName
    ? (workflows ?? []).find(workflow => workflow.name === selectedName) ?? null
    : null;

  if (error) return <div className="p-8 text-bad">Failed to load workflows: {error}</div>;
  if (workflows === null) return <div className="p-8"><Spinner label="Loading workflows…" /></div>;

  return (
    <div className="library-shell">
      <header className="library-header">
        <div className="min-w-0">
          <h1>Workflow Library</h1>
          <p>Choose a workflow based on the outcome you want to produce.</p>
        </div>
        <div className="library-header-actions">
          <button
            type="button"
            className="ui-icon-button library-nav-toggle"
            aria-label="Browse categories"
            aria-expanded={categoryNavOpen}
            onClick={() => setCategoryNavOpen(value => !value)}
          >
            <Icon name="menu" size={15} />
          </button>
          <button type="button" className="ui-button ui-button--secondary" onClick={() => setShowImport(true)}>
            <Icon name="upload" size={14} /> Import
          </button>
          <button type="button" className="ui-button ui-button--secondary" onClick={() => setShowGenerate(true)}>
            Generate from prompt
          </button>
          <button type="button" className="ui-button ui-button--primary" onClick={() => navigate('/builder')}>
            + Create workflow
          </button>
        </div>
      </header>

      <div className="library-workspace">
        {categoryNavOpen && (
          <button
            type="button"
            className="library-nav-backdrop"
            aria-label="Close categories"
            onClick={() => setCategoryNavOpen(false)}
          />
        )}
        <CategoryNav
          counts={categoryCounts}
          favoritesCount={favorites.size}
          totalCount={workflows.length}
          selection={navSelection}
          onSelect={selection => { setNavSelection(selection); setCategoryNavOpen(false); }}
          open={categoryNavOpen}
        />

        <div className="library-main">
          <LibraryToolbar
            search={search}
            onSearchChange={setSearch}
            sortKey={sortKey}
            onSortKeyChange={setSortKey}
            filters={filters}
            onFiltersChange={setFilters}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
            onRefresh={() => void loadWorkflows()}
            refreshing={refreshing}
            availableOutputs={availableOutputs}
          />

          {showFeatured && (
            <section className="library-featured">
              {lastOpened && (
                <div className="library-featured-group">
                  <h2>Continue where you left off</h2>
                  <WorkflowCard
                    workflow={lastOpened}
                    favorite={favorites.has(lastOpened.name)}
                    selected={selectedName === lastOpened.name}
                    onSelect={() => selectWorkflow(lastOpened.name)}
                    onToggleFavorite={() => toggleFavoriteFor(lastOpened.name)}
                    onOpenBuilder={() => navigate(`/builder/${lastOpened.name}`)}
                    onPrepareRun={() => setPrepareRunTarget(lastOpened)}
                    onDelete={() => setDeleteTarget(lastOpened)}
                  />
                </div>
              )}
              {recentOthers.length > 0 && (
                <div className="library-featured-group">
                  <h2>Recently used</h2>
                  <div className={`library-card-${viewMode}`}>
                    {recentOthers.map(workflow => (
                      <WorkflowCard
                        key={workflow.name}
                        workflow={workflow}
                        favorite={favorites.has(workflow.name)}
                        selected={selectedName === workflow.name}
                        onSelect={() => selectWorkflow(workflow.name)}
                        onToggleFavorite={() => toggleFavoriteFor(workflow.name)}
                        onOpenBuilder={() => navigate(`/builder/${workflow.name}`)}
                        onPrepareRun={() => setPrepareRunTarget(workflow)}
                        onDelete={() => setDeleteTarget(workflow)}
                      />
                    ))}
                  </div>
                </div>
              )}
              {approvedFeatured.length > 0 && (
                <div className="library-featured-group">
                  <h2>Eurskem approved workflows</h2>
                  <div className={`library-card-${viewMode}`}>
                    {approvedFeatured.map(workflow => (
                      <WorkflowCard
                        key={workflow.name}
                        workflow={workflow}
                        favorite={favorites.has(workflow.name)}
                        selected={selectedName === workflow.name}
                        onSelect={() => selectWorkflow(workflow.name)}
                        onToggleFavorite={() => toggleFavoriteFor(workflow.name)}
                        onOpenBuilder={() => navigate(`/builder/${workflow.name}`)}
                        onPrepareRun={() => setPrepareRunTarget(workflow)}
                        onDelete={() => setDeleteTarget(workflow)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="library-collection">
            <div className="library-collection-heading">
              <h2>{navSelection === 'all' ? 'All workflows' : navSelection === 'favorites' ? 'Favorites' : 'Workflows'}</h2>
              <span>{visible.length} workflow{visible.length === 1 ? '' : 's'}</span>
            </div>

            {visible.length === 0 ? (
              <div className="library-empty-state">
                {workflows.length === 0 ? (
                  <>
                    <strong>No workflows are available in this workspace.</strong>
                    <span>Create a workflow, import an approved template, or ask an administrator for access.</span>
                  </>
                ) : (
                  <>
                    <strong>No workflow matches all selected filters.</strong>
                    <span>Remove a filter or describe the result you want to produce.</span>
                  </>
                )}
              </div>
            ) : (
              <div className={`library-card-${viewMode}`}>
                {visible.map(workflow => (
                  <WorkflowCard
                    key={workflow.name}
                    workflow={workflow}
                    favorite={favorites.has(workflow.name)}
                    selected={selectedName === workflow.name}
                    onSelect={() => selectWorkflow(workflow.name)}
                    onToggleFavorite={() => toggleFavoriteFor(workflow.name)}
                    onOpenBuilder={() => navigate(`/builder/${workflow.name}`)}
                    onPrepareRun={() => setPrepareRunTarget(workflow)}
                    onDelete={() => setDeleteTarget(workflow)}
                  />
                ))}
              </div>
            )}
          </section>
        </div>

        {selectedWorkflow && (
          <WorkflowDetailsPanel
            workflow={selectedWorkflow}
            onClose={() => setSelectedName(null)}
            onOpenBuilder={() => navigate(`/builder/${selectedWorkflow.name}`)}
            onPrepareRun={() => setPrepareRunTarget(selectedWorkflow)}
            onDelete={() => setDeleteTarget(selectedWorkflow)}
          />
        )}
      </div>

      {prepareRunTarget && (
        <PrepareAndRunPanel
          workflow={prepareRunTarget}
          onClose={() => setPrepareRunTarget(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDeleteDialog
          workflow={deleteTarget}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={confirmDelete}
        />
      )}

      {showGenerate && <GenerateWorkflowDialog onClose={() => setShowGenerate(false)} />}

      {showImport && (
        <ImportWorkflowDialog
          onClose={() => setShowImport(false)}
          onImported={name => {
            setShowImport(false);
            void loadWorkflows().then(() => selectWorkflow(name));
          }}
        />
      )}
    </div>
  );
}
