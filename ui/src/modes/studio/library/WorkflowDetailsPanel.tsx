import { useEffect, useMemo, useState } from 'react';
import { api } from '../../../api/client';
import type { WorkflowDetail, WorkflowStats, WorkflowSummary, WorkflowVersionSummary } from '../../../api/types';
import { Icon } from '../../../components/ui/Icon';
import { Spinner } from '../../../components/Spinner';
import { humanizeIdentifier } from '../guided/runtime-model';
import { parseYaml, type YamlWorkflow } from '../yaml-bridge';
import { EvidenceTab } from './tabs/EvidenceTab';
import { OverviewTab } from './tabs/OverviewTab';
import { RunsTab } from './tabs/RunsTab';
import { StagesTab } from './tabs/StagesTab';
import { TechnicalTab } from './tabs/TechnicalTab';
import { VersionsTab } from './tabs/VersionsTab';
import { WhatItProducesTab } from './tabs/WhatItProducesTab';
import { WhatYouNeedTab } from './tabs/WhatYouNeedTab';

export type DetailsTab =
  | 'overview'
  | 'needs'
  | 'produces'
  | 'stages'
  | 'evidence'
  | 'runs'
  | 'versions'
  | 'technical';

const TABS: Array<{ id: DetailsTab; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'needs', label: 'What you need' },
  { id: 'produces', label: 'What it produces' },
  { id: 'stages', label: 'Stages and reviews' },
  { id: 'evidence', label: 'Evidence and sources' },
  { id: 'runs', label: 'Runs and performance' },
  { id: 'versions', label: 'Versions' },
  { id: 'technical', label: 'Technical details' },
];

export function WorkflowDetailsPanel({
  workflow,
  onClose,
  onOpenBuilder,
  onPrepareRun,
}: {
  workflow: WorkflowSummary;
  onClose: () => void;
  onOpenBuilder: () => void;
  onPrepareRun: () => void;
}) {
  const [tab, setTab] = useState<DetailsTab>('overview');
  const [parsed, setParsed] = useState<YamlWorkflow | null>(null);
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stats, setStats] = useState<WorkflowStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [versions, setVersions] = useState<WorkflowVersionSummary[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);

  // Structural data (YAML + fresh library/readiness) loads eagerly — it's
  // small and every tab except Runs/Versions needs it immediately.
  useEffect(() => {
    // Resets state for the newly-selected workflow, not a sync-to-external-
    // system loop — same justification as the analogous reset effects in
    // Builder.tsx's hydrateWorkflow.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setParsed(null);
    setDetail(null);
    setLoadError(null);
    let cancelled = false;
    Promise.all([
      api.getWorkflow(workflow.name).then(({ yaml }) => parseYaml(yaml)),
      api.getWorkflowDetail(workflow.name),
    ])
      .then(([parsedWorkflow, freshDetail]) => {
        if (cancelled) return;
        setParsed(parsedWorkflow);
        setDetail(freshDetail);
      })
      .catch(error => { if (!cancelled) setLoadError(String(error)); });
    return () => { cancelled = true; };
  }, [workflow.name]);

  // Runs/performance and version history are queried on demand, only when
  // that tab is actually opened — a workflow can be selected repeatedly
  // while browsing without paying for either query every time.
  useEffect(() => {
    if (tab !== 'runs' || stats || statsError) return;
    api.getWorkflowStats(workflow.name)
      .then(setStats)
      .catch(error => setStatsError(String(error)));
  }, [tab, workflow.name, stats, statsError]);

  useEffect(() => {
    if (tab !== 'versions' || versions || versionsError) return;
    api.listWorkflowVersions(workflow.name)
      .then(setVersions)
      .catch(error => setVersionsError(String(error)));
  }, [tab, workflow.name, versions, versionsError]);

  const title = detail?.library.title || workflow.library?.title || humanizeIdentifier(workflow.name);
  const library = detail?.library ?? workflow.library;
  const readiness = detail?.readiness ?? workflow.readiness;

  const activeTabContent = useMemo(() => {
    if (loadError) {
      return <div className="library-details-error">Could not load this workflow: {loadError}</div>;
    }
    if (!parsed || !library || !readiness) {
      return <div className="library-details-loading"><Spinner label="Loading workflow details…" /></div>;
    }
    switch (tab) {
      case 'overview':
        return (
          <OverviewTab
            workflow={workflow}
            library={library}
            readiness={readiness}
            parsed={parsed}
            onPrepareRun={onPrepareRun}
          />
        );
      case 'needs':
        return <WhatYouNeedTab parsed={parsed} />;
      case 'produces':
        return <WhatItProducesTab parsed={parsed} library={library} />;
      case 'stages':
        return <StagesTab parsed={parsed} />;
      case 'evidence':
        return <EvidenceTab parsed={parsed} library={library} />;
      case 'runs':
        return <RunsTab stats={stats} error={statsError} />;
      case 'versions':
        return <VersionsTab versions={versions} error={versionsError} onOpenBuilder={onOpenBuilder} />;
      case 'technical':
        return <TechnicalTab parsed={parsed} readiness={readiness} workflowName={workflow.name} />;
      default:
        return null;
    }
  }, [
    loadError, parsed, library, readiness, tab, workflow, onPrepareRun,
    stats, statsError, versions, versionsError, onOpenBuilder,
  ]);

  return (
    <aside className="library-details-panel" aria-label="Workflow details">
      <div className="library-details-header">
        <div className="min-w-0">
          <div className="library-details-eyebrow">Workflow details</div>
          <h2 className="library-details-title" title={title}>{title}</h2>
        </div>
        <button type="button" className="library-details-close" onClick={onClose} aria-label="Close workflow details">
          ×
        </button>
      </div>

      <div className="library-details-tabs" role="tablist" aria-label="Workflow detail sections">
        {TABS.map(item => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? 'is-active' : ''}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="library-details-body">
        {activeTabContent}
      </div>

      <div className="library-details-footer">
        <button type="button" className="ui-button ui-button--secondary" onClick={onOpenBuilder}>
          <Icon name="layout" size={14} /> Open in Builder
        </button>
        <button
          type="button"
          className="ui-button ui-button--primary"
          disabled={readiness?.level === 'blocked'}
          onClick={onPrepareRun}
        >
          <Icon name="play" size={14} /> Prepare and run
        </button>
      </div>
    </aside>
  );
}
