import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { NodeTypeManifest } from '../../api/types';
import { historicalNodeStatus } from './cockpit/node-render';
import { NodeInspector, type SelectedNodeInfo } from './cockpit/NodeInspector';
import { ResizeHandle } from './cockpit/ResizeHandle';
import { useResizablePanel } from './cockpit/useResizablePanel';
import { RunListPanel } from './run-history/RunListPanel';
import { RunWorkspace, type WorkspaceTab } from './run-history/RunWorkspace';
import { useRunHistoryData } from './run-history/useRunHistoryData';
import { OverviewTab } from './run-history/tabs/OverviewTab';
import { NodesTab } from './run-history/tabs/NodesTab';
import { OutputsTab } from './run-history/tabs/OutputsTab';
import { InputsTab } from './run-history/tabs/InputsTab';
import { TimelineTab } from './run-history/tabs/TimelineTab';
import { ErrorsTab } from './run-history/tabs/ErrorsTab';
import { AskAiPanel } from './run-history/AskAiPanel';

const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed']);
const VALID_TABS: WorkspaceTab[] = ['overview', 'nodes', 'outputs', 'inputs', 'timeline', 'errors', 'ask-ai'];

export function RunHistory() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const tabParam = searchParams.get('tab');
  const activeTab: WorkspaceTab = (VALID_TABS as string[]).includes(tabParam ?? '')
    ? (tabParam as WorkspaceTab)
    : 'overview';
  const selectedNodeId = searchParams.get('node');

  const data = useRunHistoryData(runId);
  const [leftCollapsed, setLeftCollapsed] = useState(() => window.innerWidth < 1150);
  const [inspectorFullscreen, setInspectorFullscreen] = useState(false);
  const [nodeTypesByName, setNodeTypesByName] = useState<Record<string, NodeTypeManifest>>({});

  useEffect(() => {
    api.nodeTypes()
      .then(types => setNodeTypesByName(Object.fromEntries(types.map(t => [t.type_name, t]))))
      .catch(() => undefined);
  }, []);

  const leftPanel = useResizablePanel({
    storageKey: 'runHistory.leftPanelWidth', defaultWidth: 320, minWidth: 240, maxWidth: 520, side: 'left',
  });
  const rightPanel = useResizablePanel({
    storageKey: 'runHistory.rightPanelWidth', defaultWidth: 384, minWidth: 300, maxWidth: 640, side: 'right',
  });

  // Nothing selected yet — land on the most recent run rather than an
  // empty page, same as before.
  useEffect(() => {
    if (!runId && data.runs.length > 0) navigate(`/workflow-runs/${data.runs[0].run_id}`, { replace: true });
  }, [runId, data.runs, navigate]);

  // A stale `?node=` from the previous run must never leak into the new
  // run's Node Inspector lookup.
  useEffect(() => {
    if (searchParams.get('node')) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete('node');
        return next;
      }, { replace: true });
    }
    // Only ever meant to fire when the run identity itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  function selectRun(nextRunId: string) {
    navigate(`/workflow-runs/${nextRunId}${tabParam ? `?tab=${tabParam}` : ''}`);
  }

  function setTab(tab: WorkspaceTab) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('tab', tab);
      return next;
    });
  }

  function selectNode(nodeId: string, tab?: WorkspaceTab) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('node', nodeId);
      if (tab) next.set('tab', tab);
      return next;
    });
  }

  const run = data.detail?.run;
  const isTerminal = run ? TERMINAL_STATUSES.has(run.status) : false;
  const selectedNodeRun = run && selectedNodeId ? run.node_runs?.[selectedNodeId] : undefined;
  const selectedNodeInfo: SelectedNodeInfo | null = (run && selectedNodeId)
    ? {
      id: selectedNodeId,
      typeName: selectedNodeRun?.type_name ?? run.node_types?.[selectedNodeId] ?? '',
      status: historicalNodeStatus(selectedNodeId, run.node_runs ?? {}, isTerminal),
    }
    : null;

  return (
    <div className="h-full flex min-w-0">
      <div style={{ width: leftCollapsed ? undefined : leftPanel.width }} className="flex-none">
        <RunListPanel
          runs={data.runs}
          listErr={data.listErr}
          selectedRunId={runId}
          onSelect={selectRun}
          onRefresh={data.refresh}
          collapsed={leftCollapsed}
          onToggleCollapsed={() => setLeftCollapsed((v) => !v)}
        />
      </div>
      {!leftCollapsed && <ResizeHandle {...leftPanel.handleProps} dragging={leftPanel.dragging} />}

      <div className="flex-1 min-w-0 min-h-0">
        {data.detailErr && (
          <div className="m-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {data.detailErr.includes('404') ? 'Run not found.' : `Couldn't load this run. ${data.detailErr}`}
          </div>
        )}
        {!runId && !data.detailErr && (
          <div className="p-8 text-ink-500 text-sm">Select a run to view its detail.</div>
        )}
        {runId && !data.detail && !data.detailErr && (
          <div className="p-8 text-ink-500 text-sm">Loading…</div>
        )}
        {data.detail && (
          <RunWorkspace
            detail={data.detail}
            actionBusy={data.actionBusy}
            actionErr={data.actionErr}
            retryErr={data.retryErr}
            autofixErr={data.autofixErr}
            onPause={data.pauseRun}
            onResume={data.resumeRun}
            onRestart={data.restartRun}
            onDelete={data.deleteRun}
            onRetry={data.retryFailedRun}
            onOpenInCockpit={() => data.openInCockpit(selectedNodeId)}
            onAutofix={() => void data.autofixAndOpenInBuilder()}
            autofixBusy={data.autofixBusy}
            onOpenProposalReview={() => navigate(`/proposal-review/${data.detail!.run.run_id}`)}
            onOpenEvidence={() => navigate(`/candidates/${data.detail!.run.run_id}`)}
            activeTab={activeTab}
            onTabChange={setTab}
          >
            {activeTab === 'overview' && (
              <OverviewTab
                run={data.detail.run}
                audit={data.detail.audit}
                onInspectNode={(nodeId) => selectNode(nodeId, 'nodes')}
                onRetry={data.retryFailedRun}
              />
            )}
            {activeTab === 'nodes' && (
              <NodesTab run={data.detail.run} selectedNodeId={selectedNodeId} onSelectNode={selectNode} />
            )}
            {activeTab === 'outputs' && (
              <OutputsTab run={data.detail.run} onOpenNode={selectNode} />
            )}
            {activeTab === 'inputs' && <InputsTab run={data.detail.run} />}
            {activeTab === 'timeline' && (
              <TimelineTab audit={data.detail.audit} onSelectNode={(nodeId) => selectNode(nodeId, 'nodes')} />
            )}
            {activeTab === 'errors' && (
              <ErrorsTab run={data.detail.run} onInspectNode={(nodeId) => selectNode(nodeId, 'nodes')} />
            )}
            {/* Keyed on run_id: selecting a different run in the sidebar
                does not change `activeTab`, so without this key this panel
                would stay mounted across runs and briefly carry the
                previous run's conversation as `turns` — reachable if a
                question is sent while the history refetch for the newly
                selected run is still in flight. A `key` forces a full
                remount on selection change instead. */}
            {activeTab === 'ask-ai' && <AskAiPanel key={data.detail.run.run_id} runId={data.detail.run.run_id} />}
          </RunWorkspace>
        )}
      </div>

      {inspectorFullscreen ? (
        <div className="fixed inset-0 z-50 bg-white">
          <NodeInspector
            selectedNode={selectedNodeInfo}
            nodeRun={selectedNodeRun}
            run={run ?? null}
            navigate={navigate}
            workflowVariables={{
              inputs: run?.inputs ?? {},
              variables: run?.variables ?? {},
              outputs: run?.outputs ?? {},
            }}
            fullscreen
            onToggleFullscreen={() => setInspectorFullscreen(false)}
            live={false}
            nodeTypesByName={nodeTypesByName}
          />
        </div>
      ) : (
        <>
          <ResizeHandle {...rightPanel.handleProps} dragging={rightPanel.dragging} />
          <aside style={{ width: rightPanel.width }} className="flex-none border-l border-slate-200 bg-white overflow-hidden">
            <NodeInspector
              selectedNode={selectedNodeInfo}
              nodeRun={selectedNodeRun}
              run={run ?? null}
              navigate={navigate}
              workflowVariables={{
                inputs: run?.inputs ?? {},
                variables: run?.variables ?? {},
                outputs: run?.outputs ?? {},
              }}
              fullscreen={false}
              onToggleFullscreen={() => setInspectorFullscreen(true)}
              live={false}
              nodeTypesByName={nodeTypesByName}
            />
          </aside>
        </>
      )}
    </div>
  );
}
