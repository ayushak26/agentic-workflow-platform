/* Runtime node payloads are intentionally plugin-defined and heterogeneous. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { CockpitNode, type CockpitNodeData } from './CockpitNode';
import { HITLPanel } from './HITLPanel';
import { OutputViewer } from './OutputViewer';
import { yamlToReactFlow } from './yaml-bridge';
import {
  applyCancellation,
  computePathHighlight,
  computeReachability,
  type GraphEdge,
  type NodeStatus,
} from './cockpit-state';
import { layoutFlow, type Stage } from './flow-layout';
import { useCockpitRun } from './cockpit/useCockpitRun';
import { OverviewPanel, type OverviewNode } from './cockpit/OverviewPanel';
import { NodeInspector, type SelectedNodeInfo } from './cockpit/NodeInspector';
import { useResizablePanel } from './cockpit/useResizablePanel';
import { ResizeHandle } from './cockpit/ResizeHandle';
import { shortDuration, outputSummary } from './cockpit/node-render';
import {
  applyStageCollapse,
  buildStageBandNodes,
  STAGE_PLACEHOLDER_TYPE,
} from './cockpit/graph-collapse';
import { STAGE_BAND_TYPE, StageBandNode } from './cockpit/StageBandNode';
import { StagePlaceholderNode } from './cockpit/StagePlaceholderNode';
import type { PipelineRunDetail } from '../../api/types';

const nodeTypes = {
  workflow: CockpitNode,
  [STAGE_PLACEHOLDER_TYPE]: StagePlaceholderNode,
  [STAGE_BAND_TYPE]: StageBandNode,
};

const STATUS_BADGE: Record<string, string> = {
  connecting: 'bg-slate-200 text-ink-700',
  running: 'bg-accent-600 text-white',
  paused: 'bg-warn text-white',
  completed: 'bg-ok text-white',
  rejected: 'bg-warn text-white',
  failed: 'bg-bad text-white',
};

export function Cockpit() {
  const run = useCockpitRun();
  const {
    runId, navState, parsedWf, navigate, triggerError, liveRun,
    gate, gateHidden, setGateHidden, gateFetchError, retryGateFetch,
    finished, streamError, cockpit, activeNodeId, reusedNodeCount,
    applyResumeResult, pipelineDoc, continueToNextStage, continuingStage,
    continueError, liveRunNodeStatus,
  } = run;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<CockpitNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [stages, setStages] = useState<Stage[]>([]);
  const [followRunning, setFollowRunning] = useState(true);
  const [collapsedStages, setCollapsedStages] = useState<Set<number>>(new Set());
  const [showOnlyActive, setShowOnlyActive] = useState(false);
  const [fullscreenGraph, setFullscreenGraph] = useState(false);
  const [fullscreenOutput, setFullscreenOutput] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(false);

  const leftPanel = useResizablePanel({
    storageKey: 'cockpit.leftPanelWidth', defaultWidth: 280, minWidth: 220, maxWidth: 480, side: 'left',
  });
  const rightPanel = useResizablePanel({
    storageKey: 'cockpit.rightPanelWidth', defaultWidth: 384, minWidth: 300, maxWidth: 640, side: 'right',
  });

  // Build and arrange the graph once for this workflow — never re-run on a
  // status tick, so node positions stay fixed for the life of a run.
  useEffect(() => {
    if (!parsedWf) return;
    const base = yamlToReactFlow(parsedWf);
    const initialNodes = base.nodes.map((n) => ({
      ...n,
      data: { ...n.data, status: 'pending' as NodeStatus },
    }));
    const laidOut = layoutFlow(initialNodes, base.edges);
    setNodes(laidOut.nodes);
    setEdges(base.edges);
    // Structural graph state derived from parsedWf (which itself never
    // changes after mount) — a legitimate case of syncing derived state,
    // not a cascading-render risk.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStages(laidOut.stages);
  }, [parsedWf, setEdges, setNodes]);

  const plainEdges: GraphEdge[] = useMemo(() => edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: typeof e.label === 'string' ? e.label : undefined,
  })), [edges]);

  // SSE events + polled run detail change status without resetting the
  // arranged positions. Only the node(s) whose computed status actually
  // changed get a new `data` object — everything else keeps its exact prior
  // object reference so CockpitNode's React.memo can skip re-rendering it.
  useEffect(() => {
    setNodes((current) => {
      const rawStatuses: Record<string, NodeStatus> = {};
      for (const node of current) {
        const ssStatus = cockpit.nodeStates[node.data.nodeId];
        const status = ssStatus && ssStatus !== 'pending'
          ? ssStatus
          : liveRunNodeStatus(node.data.nodeId) ?? ssStatus ?? 'pending';
        rawStatuses[node.data.nodeId] = status;
      }
      const withSkips = computeReachability(
        current.map((n) => ({ id: n.data.nodeId })),
        plainEdges,
        rawStatuses,
        liveRun?.outputs ?? {},
      );
      const runEnded = (
        finished != null
        || liveRun?.status === 'failed'
        || liveRun?.status === 'rejected'
      );
      const finalStatuses = applyCancellation(withSkips, runEnded);

      return current.map((node) => {
        const status = finalStatuses[node.data.nodeId];
        const nodeRun = liveRun?.node_runs?.[node.data.nodeId];
        const durationLabel = shortDuration(nodeRun?.duration_s);
        const summary = nodeRun?.output != null ? outputSummary(nodeRun.output) : null;
        const hasError = status === 'failed';
        if (
          status === node.data.status
          && durationLabel === node.data.durationLabel
          && summary === node.data.outputSummary
          && hasError === node.data.hasError
        ) {
          return node;
        }
        return {
          ...node,
          data: {
            ...node.data, status, durationLabel, outputSummary: summary, hasError,
          },
        };
      });
    });
  }, [cockpit.nodeStates, liveRun, finished, plainEdges, setNodes, liveRunNodeStatus]);

  const stageIndexById = useMemo(() => {
    const map = new Map<string, number>();
    for (const stage of stages) for (const id of stage.nodeIds) map.set(id, stage.index);
    return map;
  }, [stages]);

  const pathHighlight = useMemo(
    () => computePathHighlight(selectedId, plainEdges),
    [selectedId, plainEdges],
  );

  const filteredOutIds = useMemo(() => {
    if (!showOnlyActive) return new Set<string>();
    return new Set(nodes.filter((n) => n.data.status !== 'active').map((n) => n.data.nodeId));
  }, [nodes, showOnlyActive]);

  const collapsedResult = useMemo(
    () => applyStageCollapse(nodes, edges, stages, collapsedStages),
    [nodes, edges, stages, collapsedStages],
  );
  const bandNodes = useMemo(
    () => buildStageBandNodes(stages, collapsedStages),
    [stages, collapsedStages],
  );

  const displayNodes: Node<any>[] = useMemo(() => {
    const rendered = collapsedResult.nodes.map((node) => {
      if (node.type === STAGE_PLACEHOLDER_TYPE) return node;
      const nodeId = node.data.nodeId;
      const faded = (
        (selectedId != null && !pathHighlight.has(nodeId))
        || filteredOutIds.has(nodeId)
      );
      const pathHighlighted = selectedId != null && nodeId !== selectedId && pathHighlight.has(nodeId);
      if (node.data.faded === faded && node.data.pathHighlighted === pathHighlighted) return node;
      return { ...node, data: { ...node.data, faded, pathHighlighted } };
    });
    return [...bandNodes, ...rendered];
  }, [collapsedResult.nodes, bandNodes, selectedId, pathHighlight, filteredOutIds]);

  const displayEdges: Edge[] = useMemo(() => collapsedResult.edges.map((edge) => {
    const onPath = selectedId != null && pathHighlight.has(edge.source) && pathHighlight.has(edge.target);
    const isActiveEdge = activeNodeId != null && edge.source === activeNodeId;
    const faded = selectedId != null && !onPath;
    return {
      ...edge,
      type: 'smoothstep',
      animated: isActiveEdge,
      style: {
        stroke: onPath ? '#4f46e5' : '#cbd5e1',
        strokeWidth: onPath ? 2.5 : 1.5,
        opacity: faded ? 0.25 : 1,
      },
    };
  }), [collapsedResult.edges, selectedId, pathHighlight, activeNodeId]);

  const focusNode = useCallback((nodeId: string | null) => {
    if (!nodeId || !rfInstance) return;
    const node = nodes.find((candidate) => candidate.data.nodeId === nodeId);
    if (!node) return;
    setSelectedId(nodeId);
    rfInstance.setCenter(
      node.position.x + (node.width ?? 260) / 2,
      node.position.y + (node.height ?? 92) / 2,
      { zoom: 1.15, duration: 450 },
    );
  }, [nodes, rfInstance]);

  // Preselect the node the user was inspecting in Run History before
  // clicking "Open in Cockpit" — once, as soon as the graph and the
  // ReactFlow instance are both ready. Guarded so it never fires again
  // (e.g. after the user deliberately deselects later in the session).
  const appliedInitialSelection = useRef(false);
  useEffect(() => {
    if (appliedInitialSelection.current) return;
    if (!navState.selectedNodeId || !rfInstance || nodes.length === 0) return;
    appliedInitialSelection.current = true;
    // One-time selection carried over from Run History, guarded above so
    // it can only ever run once per mount — not a cascading-render risk.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    focusNode(navState.selectedNodeId);
  }, [navState.selectedNodeId, rfInstance, nodes, focusNode]);

  useEffect(() => {
    if (!followRunning || !activeNodeId) return;
    requestAnimationFrame(() => focusNode(activeNodeId));
  }, [activeNodeId, focusNode, followRunning]);

  const fitToScreen = useCallback(() => {
    rfInstance?.fitView({ padding: 0.2, duration: 400 });
  }, [rfInstance]);

  const focusSelectedPath = useCallback(() => {
    if (!selectedId) return;
    rfInstance?.fitView({
      nodes: [...pathHighlight].map((id) => ({ id })),
      padding: 0.25,
      duration: 400,
    });
  }, [selectedId, pathHighlight, rfInstance]);

  const showFailedPath = useCallback(() => {
    const failedNode = nodes.find((n) => n.data.status === 'failed');
    if (!failedNode) return;
    const id = failedNode.data.nodeId;
    setSelectedId(id);
    const highlighted = computePathHighlight(id, plainEdges);
    requestAnimationFrame(() => {
      rfInstance?.fitView({ nodes: [...highlighted].map((nid) => ({ id: nid })), padding: 0.25, duration: 400 });
    });
  }, [nodes, plainEdges, rfInstance]);

  const hasFailedNode = useMemo(() => nodes.some((n) => n.data.status === 'failed'), [nodes]);

  const keyboardOrder = useMemo(() => (
    [...nodes]
      .sort((a, b) => {
        const sa = stageIndexById.get(a.data.nodeId) ?? 0;
        const sb = stageIndexById.get(b.data.nodeId) ?? 0;
        if (sa !== sb) return sa - sb;
        return a.position.y - b.position.y;
      })
      .map((n) => n.data.nodeId)
  ), [nodes, stageIndexById]);

  const onGraphKeyDown = useCallback((e: KeyboardEvent) => {
    if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(e.key)) return;
    if (keyboardOrder.length === 0) return;
    e.preventDefault();
    const currentIndex = selectedId ? keyboardOrder.indexOf(selectedId) : -1;
    const forward = e.key === 'ArrowRight' || e.key === 'ArrowDown';
    const nextIndex = currentIndex === -1
      ? 0
      : ((currentIndex + (forward ? 1 : -1)) + keyboardOrder.length) % keyboardOrder.length;
    focusNode(keyboardOrder[nextIndex]);
  }, [keyboardOrder, selectedId, focusNode]);

  const onNodeClick = useCallback((_: unknown, node: Node<any>) => {
    if (node.type === STAGE_PLACEHOLDER_TYPE) {
      setCollapsedStages((prev) => {
        const next = new Set(prev);
        next.delete(node.data.stageIndex);
        return next;
      });
      return;
    }
    setSelectedId(node.data.nodeId);
  }, []);

  const selectedNodeInfo: SelectedNodeInfo | null = useMemo(() => {
    if (!selectedId) return null;
    const node = nodes.find((n) => n.data.nodeId === selectedId);
    if (!node) return null;
    return { id: selectedId, typeName: node.data.typeName, status: node.data.status };
  }, [selectedId, nodes]);

  const overviewNodes: OverviewNode[] = useMemo(() => (
    nodes.map((n) => ({ id: n.data.nodeId, typeName: n.data.typeName, status: n.data.status }))
  ), [nodes]);

  const workflowVariables = useMemo(() => ({
    inputs: liveRun?.inputs ?? navState.inputs ?? {},
    variables: (
      liveRun?.variables
      ?? Object.fromEntries((parsedWf?.static_variables ?? []).map((item) => [item.name, item.value]))
    ),
    outputs: liveRun?.outputs ?? {},
  }), [liveRun, navState.inputs, parsedWf]);

  const pipelineBanner = navState.pipeline && (
    <PipelineStageBanner
      pipeline={navState.pipeline}
      pipelineDoc={pipelineDoc}
      onContinue={continueToNextStage}
      continuing={continuingStage}
      continueError={continueError}
      onViewOverview={() => navigate(`/pipelines/runs/${navState.pipeline!.pipelineRunId}`)}
    />
  );

  // ✅ early returns go AFTER all hooks
  if (finished?.status === 'completed') {
    return (
      <div className="h-full flex flex-col">
        {pipelineBanner}
        <div className="flex-1 min-h-0">
          <OutputViewer
            runId={runId}
            state={finished.state}
            projectedOutput={finished.output}
            workflowName={navState.workflowName ?? parsedWf?.name}
          />
        </div>
      </div>
    );
  }

  if (!runId) {
    return <div className="p-8 text-ink-500">No run id in URL.</div>;
  }
  if (!navState.workflowYaml) {
    return (
      <div className="p-8">
        <div className="text-bad">No workflow YAML in navigation state.</div>
        <div className="text-ink-500 text-sm mt-2">
          Cockpits are launched from the Library's Run button. Direct navigation isn't supported yet
          (Phase 11 will add a snapshot endpoint that lets you reattach).
        </div>
        <button
          onClick={() => navigate('/library')}
          className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm"
        >
          Back to Library
        </button>
      </div>
    );
  }
  if (!parsedWf) {
    return (
      <div className="p-8">
        <div className="text-ink-500">Parsing workflow…</div>
      </div>
    );
  }

  const showHITL = gate !== null && finished === null && !gateHidden;
  const gateDismissed = gate !== null && finished === null && gateHidden;
  const showGateError = (
    navState.attach
    && !showHITL
    && !finished
    && liveRun?.status === 'paused'
    && liveRun.pause_kind !== 'user_requested'
    && gateFetchError !== null
  );
  const displayStatus = finished?.status ?? (gate ? 'paused' : cockpit.runStatus);

  const inspector = (
    <NodeInspector
      selectedNode={selectedNodeInfo}
      nodeRun={selectedId ? liveRun?.node_runs?.[selectedId] : undefined}
      streamingPreview={selectedId ? cockpit.outputPreviews[selectedId] : undefined}
      run={liveRun}
      navigate={navigate}
      workflowVariables={workflowVariables}
      fullscreen={fullscreenOutput}
      onToggleFullscreen={() => setFullscreenOutput((v) => !v)}
    />
  );

  return (
    <div className="h-full flex flex-col">
      {pipelineBanner}
      <div className="flex-1 flex min-h-0">
        {!fullscreenGraph && !fullscreenOutput && (
          <>
            <div style={{ width: leftCollapsed ? undefined : leftPanel.width }} className="flex-none">
              <OverviewPanel
                workflowName={navState.workflowName ?? parsedWf.name}
                runStatus={displayStatus}
                startedAt={liveRun?.started_at ?? null}
                endedAt={liveRun?.ended_at ?? null}
                nodes={overviewNodes}
                selectedId={selectedId}
                onSelect={focusNode}
                collapsed={leftCollapsed}
                onToggleCollapsed={() => setLeftCollapsed((v) => !v)}
              />
            </div>
            {!leftCollapsed && <ResizeHandle {...leftPanel.handleProps} dragging={leftPanel.dragging} />}
          </>
        )}

        <div
          className="flex-1 relative min-w-0"
          tabIndex={0}
          onKeyDown={onGraphKeyDown}
        >
          <ReactFlow
            nodes={displayNodes}
            edges={displayEdges}
            nodeTypes={nodeTypes}
            onInit={setRfInstance}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onPaneClick={() => setSelectedId(null)}
            fitView
            nodesDraggable={false}
            nodesConnectable={false}
            edgesUpdatable={false}
            minZoom={0.1}
          >
            <Background gap={20} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>

          <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-md px-3 py-2 shadow-sm border border-slate-200 max-w-sm">
            <div className="text-xs text-ink-500 font-mono">run {runId.slice(0, 8)}…</div>
            {navState.retrySourceRunId && (
              <div className="text-xs text-cyan-700 mt-1">
                Retry: {reusedNodeCount} completed node{reusedNodeCount === 1 ? '' : 's'} reused
              </div>
            )}
            {activeNodeId && (
              <div className="text-xs text-accent-600 mt-1 font-medium">
                Running: <span className="font-mono">{activeNodeId}</span>
              </div>
            )}
            {streamError && (
              <div className="text-xs text-ink-500 mt-1">
                live feed reconnecting (gates still work)
              </div>
            )}
            {triggerError && <div className="text-xs text-bad mt-1">{triggerError}</div>}
            <span className={`inline-block mt-1 text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 ${STATUS_BADGE[displayStatus]}`}>
              {displayStatus}
            </span>
          </div>

          <div className="absolute top-4 right-4 flex flex-wrap items-center gap-1.5 justify-end max-w-[70%]">
            {gateDismissed && (
              <button
                onClick={() => setGateHidden(false)}
                className="px-3 py-2 rounded-md border border-warn bg-warn/10 text-warn text-sm font-medium animate-pulse"
              >
                Review paused node: <span className="font-mono">{gate!.nodeId}</span>
              </button>
            )}
            <ToolbarButton onClick={() => setFollowRunning((v) => !v)} active={followRunning}>
              Follow execution {followRunning ? 'on' : 'off'}
            </ToolbarButton>
            <ToolbarButton onClick={() => focusNode(activeNodeId)} disabled={!activeNodeId}>
              Focus running
            </ToolbarButton>
            <ToolbarButton onClick={fitToScreen}>Fit to screen</ToolbarButton>
            <ToolbarButton onClick={fitToScreen}>Reset view</ToolbarButton>
            <ToolbarButton onClick={focusSelectedPath} disabled={!selectedId}>
              Focus selected path
            </ToolbarButton>
            <ToolbarButton onClick={() => setShowOnlyActive((v) => !v)} active={showOnlyActive}>
              Only active
            </ToolbarButton>
            <ToolbarButton onClick={showFailedPath} disabled={!hasFailedNode}>
              Show failed path
            </ToolbarButton>
            <ToolbarButton onClick={() => setCollapsedStages(new Set(stages.map((s) => s.index)))}>
              Collapse groups
            </ToolbarButton>
            <ToolbarButton onClick={() => setCollapsedStages(new Set())}>
              Expand groups
            </ToolbarButton>
            <ToolbarButton onClick={() => setFullscreenGraph((v) => !v)} active={fullscreenGraph}>
              {fullscreenGraph ? 'Exit full screen' : 'Full-screen graph'}
            </ToolbarButton>
          </div>
        </div>

        {fullscreenOutput ? (
          <div className="fixed inset-0 z-50 bg-white">{inspector}</div>
        ) : !fullscreenGraph && (
          <>
            <ResizeHandle {...rightPanel.handleProps} dragging={rightPanel.dragging} />
            <aside
              style={{ width: showHITL ? Math.max(rightPanel.width, 560) : rightPanel.width }}
              className="flex-none border-l border-slate-200 bg-white overflow-hidden"
            >
              {showHITL ? (
                <div className="h-full overflow-y-auto">
                  <HITLPanel
                    key={`${runId}:${gate!.nodeId}`}
                    runId={runId}
                    pausedNodeId={gate!.nodeId}
                    question={gate!.question}
                    context={gate!.context}
                    allowedActions={gate!.allowedActions}
                    content={gate!.content}
                    allowDocumentOverride={gate!.allowDocumentOverride}
                    maxEditChars={gate!.maxEditChars}
                    onResult={applyResumeResult}
                    onSubmitting={() => setGateHidden(true)}
                    onSubmitError={() => undefined}
                    onClose={() => setGateHidden(true)}
                  />
                </div>
              ) : showGateError ? (
                <div className="p-6">
                  <div className="text-bad font-medium">Couldn't load this run's review gate</div>
                  <div className="text-sm text-ink-500 mt-1">{gateFetchError}</div>
                  <button
                    onClick={retryGateFetch}
                    className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500"
                  >
                    Retry
                  </button>
                </div>
              ) : finished ? (
                <div className="p-6">
                  {finished.status === 'rejected' ? (
                    <>
                      <div className="text-warn font-medium">Workflow rejected</div>
                      <div className="text-sm text-ink-500 mt-1">
                        Rejected at <span className="font-mono">{finished.node}</span>
                        {finished.reason ? ` — ${finished.reason}` : ''}
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="text-bad font-medium">Workflow failed: {finished.error}</div>
                      <button
                        onClick={() => {
                          const failedNodeId = nodes.find((n) => n.data.status === 'failed')?.data.nodeId;
                          const query = failedNodeId ? `?node=${encodeURIComponent(failedNodeId)}` : '';
                          navigate(`/history/${runId}${query}`);
                        }}
                        className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500"
                      >
                        Open Run History and retry
                      </button>
                    </>
                  )}
                </div>
              ) : inspector}
            </aside>
          </>
        )}
      </div>
    </div>
  );
}

function ToolbarButton({
  onClick, disabled, active, children,
}: {
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-2.5 py-1.5 rounded-md border text-xs whitespace-nowrap ${
        active
          ? 'border-accent-600 bg-accent-50 text-accent-700'
          : 'border-slate-300 bg-white text-ink-700 hover:bg-slate-50'
      } disabled:opacity-40`}
    >
      {children}
    </button>
  );
}

function PipelineStageBanner({
  pipeline,
  pipelineDoc,
  onContinue,
  continuing,
  continueError,
  onViewOverview,
}: {
  pipeline: NonNullable<ReturnType<typeof useCockpitRun>['navState']['pipeline']>;
  pipelineDoc: PipelineRunDetail | null;
  onContinue: () => void;
  continuing: boolean;
  continueError: string | null;
  onViewOverview: () => void;
}) {
  const nextStage = pipelineDoc?.status === 'gated'
    ? pipelineDoc.stages[pipelineDoc.current_stage_index + 1]
    : null;

  return (
    <div className="flex-none px-4 py-2.5 bg-cyan-50 border-b border-cyan-200 flex items-center justify-between gap-3">
      <div className="text-xs text-cyan-900 min-w-0">
        <span className="font-semibold">{pipeline.pipelineName ?? pipelineDoc?.pipeline_name ?? 'Pipeline'}</span>
        {' '}— stage {pipeline.stageIndex + 1}/{pipeline.totalStages} · <span className="font-mono">{pipeline.stageId}</span>
        {!pipelineDoc && ' · loading status…'}
        {pipelineDoc?.status === 'gated' && !nextStage && ' · complete'}
        {pipelineDoc?.status === 'completed' && ' · all stages complete'}
        {pipelineDoc?.status === 'failed' && ' · pipeline stopped'}
        {continueError && <span className="text-red-700 ml-2">{continueError}</span>}
      </div>
      <div className="flex-none flex items-center gap-2">
        {nextStage && (
          <button
            type="button"
            onClick={onContinue}
            disabled={continuing}
            className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500 disabled:opacity-50"
          >
            {continuing ? `Opening ${nextStage.id}…` : `Continue to ${nextStage.id}`}
          </button>
        )}
        <button
          type="button"
          onClick={onViewOverview}
          className="px-3 py-1.5 rounded-md border border-cyan-300 bg-white text-cyan-800 text-xs hover:bg-cyan-50"
        >
          Pipeline overview
        </button>
      </div>
    </div>
  );
}
