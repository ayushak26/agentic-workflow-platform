/* Runtime node payloads are intentionally plugin-defined and heterogeneous. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useEdgesState,
  useNodesState,
  type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { api } from '../../api/client';
import { useRunEvents } from '../../hooks/useRunEvents';
import { CopyButton } from '../../components/CopyButton';
import { Spinner } from '../../components/Spinner';
import { CockpitNode } from './CockpitNode';
import { artifactLabel, fileKey } from './file-artifact';
import { HITLPanel } from './HITLPanel';
import { OutputViewer } from './OutputViewer';
import { WorkflowVariablesPanel } from './WorkflowVariablesPanel';
import { parseYaml, yamlToReactFlow, type WorkflowNodeData, type YamlWorkflow } from './yaml-bridge';
import { deriveCockpitState, type NodeStatus } from './cockpit-state';
import { useSetRunCost } from "../../RunCostContext";
import { layoutFlow } from './flow-layout';
import type { HITLReviewContent, NodeRunStatus, PipelineRunDetail, RunDetail } from '../../api/types';

type CockpitNodeData = WorkflowNodeData & { status: NodeStatus };
const nodeTypes = { workflow: CockpitNode };

const STATUS_BADGE: Record<string, string> = {
  connecting: 'bg-slate-200 text-ink-700',
  running: 'bg-accent-600 text-white',
  paused: 'bg-warn text-white',
  completed: 'bg-ok text-white',
  rejected: 'bg-warn text-white',
  failed: 'bg-bad text-white',
};

type Gate = {
  nodeId: string;
  context: unknown;
  question: string;
  allowedActions: string[];
  content: HITLReviewContent | null;
  allowDocumentOverride: boolean;
  maxEditChars: number;
};

type Finished = {
  status: 'completed' | 'failed' | 'rejected';
  state?: any;
  output?: Record<string, unknown>;
  error?: string;
  node?: string;
  reason?: string;
};

// Present when this Cockpit is running one stage of a pipeline rather than a
// standalone workflow. 'start' triggers POST /pipelines/run (stage 0 of a
// fresh pipeline run); 'advance' triggers POST /pipelines/{id}/advance (any
// later stage, reached via the "Continue to next stage" action).
type PipelineNavState = {
  mode: 'start' | 'advance';
  pipelineYaml?: string;
  pipelineRunId: string;
  pipelineName?: string;
  stageId: string;
  stageIndex: number;
  totalStages: number;
};

// Fallback node coloring for attach mode, when SSE replay has nothing (the
// event bus is a bounded in-memory buffer and may have evicted this run's
// history, or the backend process restarted since it paused).
const NODE_RUN_STATUS_MAP: Record<NodeRunStatus, NodeStatus> = {
  running: 'active',
  paused: 'paused',
  completed: 'done',
  reused: 'reused',
  failed: 'failed',
};

function liveRunNodeStatus(nodeId: string, liveRun: RunDetail | null): NodeStatus | null {
  if (!liveRun) return null;
  const nodeRunStatus = liveRun.node_runs?.[nodeId]?.status;
  if (nodeRunStatus) return NODE_RUN_STATUS_MAP[nodeRunStatus];
  if (liveRun.active_nodes?.includes(nodeId)) return 'active';
  if (nodeId in (liveRun.outputs ?? {})) return 'done';
  return null;
}

export function Cockpit() {
  const { runId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  // Snapshot navigation state ONCE so the component binds to a stable run for its
  // whole life — re-deriving it each render could remount the SSE stream.
  const [navState] = useState(
    () =>
      (location.state ?? {}) as {
        workflowYaml?: string;
        inputs?: Record<string, unknown>;
        workflowName?: string;
        retrySourceRunId?: string;
        pipeline?: PipelineNavState;
        // Reopening an existing run (Run History's "Open in Cockpit") rather
        // than launching a new one. Skips triggering run/resume/pipeline —
        // this run already exists — and instead reconstructs graph + HITL
        // gate state from durable data (SSE replay + polled run detail +
        // the pending-gate endpoint) so it works even from a fresh page load.
        attach?: boolean;
      }
  );

  const [parsedWf] = useState<YamlWorkflow | null>(() => (
    navState.workflowYaml ? parseYaml(navState.workflowYaml) : null
  ));
  const [runTriggered, setRunTriggered] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<CockpitNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [followRunning, setFollowRunning] = useState(true);
  const [sidebarTab, setSidebarTab] = useState<'variables' | 'node'>('variables');
  const [liveRun, setLiveRun] = useState<RunDetail | null>(null);

  // HITL gates and final result are driven off the run/resume HTTP responses,
  // NOT the SSE feed — so approvals work even if the stream drops mid-run.
  const [gate, setGate] = useState<Gate | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const setRunCost = useSetRunCost();

  const {
    events,
    open: streamOpen,
    error: streamError,
  } = useRunEvents(runId ?? null);

  // Apply a run/resume response: advance to the next gate, or finish.
  function applyResumeResult(res: any) {
    if (!res) return;
    if (res.status === 'paused') {
      const interrupt = (
        res.state?.__interrupt__?.[0]
        ?? res.interrupt?.[0]
        ?? null
      );
      const v = interrupt?.value ?? interrupt;
      if (v) {
        setGate({
          nodeId: v.node_id,
          context: v.context,
          question: v.question ?? '',
          allowedActions: v.allowed_actions ?? ['approve', 'reject'],
          content: v.content ?? null,
          allowDocumentOverride: v.allow_document_override ?? true,
          maxEditChars: v.max_edit_chars ?? 1_000_000,
        });
      }
    } else if (res.status === 'completed') {
      setGate(null);
      setFinished({
        status: 'completed',
        state: res.state,
        output: res.output,
      });
    } else if (res.status === 'failed') {
      setGate(null);
      setFinished({ status: 'failed', error: res.error });
    } else if (res.status === 'rejected') {
      setGate(null);
      setFinished({ status: 'rejected', node: res.node_id, reason: res.reason });
    }
  }
  // Fetch run cost whenever the run reaches a completed state, regardless of
// which path (HTTP resume vs SSE) marked it finished.
useEffect(() => {
  if (finished?.status === 'completed' && runId) {
    api.costForRun(runId)
      .then(c => setRunCost(c.total_usd))
      .catch(e => console.error('cost fetch failed', e));
  }
}, [finished, runId, setRunCost]);
  // Subscribe first, then trigger exactly once so no early SSE event is lost.
  useEffect(() => {
    if (navState.attach || !streamOpen || runTriggered || !navState.workflowYaml || !runId) return;
    // This state guards the one external run request owned by this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRunTriggered(true);
    const pipeline = navState.pipeline;
    const request = pipeline
      ? pipeline.mode === 'advance'
        ? api.advancePipeline(pipeline.pipelineRunId, undefined, runId)
        : api.runPipeline(
            pipeline.pipelineYaml!,
            navState.inputs ?? {},
            undefined,
            pipeline.pipelineRunId,
            runId,
          )
      : navState.retrySourceRunId
      ? api.retryFailedRun(navState.retrySourceRunId, runId)
      // Do not send a made-up "default" session. The API derives the durable
      // history/retrieval scope from the authenticated user.
      : api.runWorkflow(
          navState.workflowYaml,
          navState.inputs ?? {},
          undefined,
          runId,
        );
    request
      // A pipeline call's result is nested under stage_result — the same
      // {status, run_id, state, ...} shape run_workflow returns directly.
      .then((res) => applyResumeResult(pipeline ? (res as any).stage_result : res))
      .catch((e) => {
        const message = String(e.message ?? e);
        setTriggerError(message);
        setFinished({ status: 'failed', error: message });
      });
  }, [
    navState.attach,
    streamOpen,
    runTriggered,
    navState.workflowYaml,
    navState.inputs,
    navState.retrySourceRunId,
    navState.pipeline,
    runId,
  ]);

  // Exact inputs and completed outputs are persisted incrementally in run
  // history. Polling that record powers the Variables panel while the graph is
  // still executing; SSE previews remain intentionally small.
  useEffect(() => {
    if (!runId || finished) return;
    if (!runTriggered && !navState.attach) return;
    let cancelled = false;
    const load = () => {
      api.runDetail(runId)
        .then((result) => {
          if (!cancelled) setLiveRun(result.run);
        })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [finished, runId, runTriggered, navState.attach]);

  // Attach mode never gets a run/resume HTTP response of its own to read a
  // terminal status off (see applyResumeResult) — it has to notice the run
  // finished via the same runDetail poll that drives the Variables panel.
  useEffect(() => {
    if (!navState.attach || finished) return;
    const result: Finished | null = (
      liveRun?.status === 'completed'
        ? {
          status: 'completed',
          state: {
            node_outputs: liveRun.outputs,
            inputs: liveRun.inputs,
            variables: liveRun.variables,
          },
        }
        : liveRun?.status === 'failed'
        ? { status: 'failed', error: liveRun.error ?? undefined }
        : liveRun?.status === 'rejected'
        ? {
          status: 'rejected',
          node: liveRun.failed_node ?? undefined,
          reason: liveRun.error ?? undefined,
        }
        : null
    );
    if (!result) return;
    // Synchronizing local state from a polled external record (runDetail),
    // not from a prop/state change this render already reflects.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFinished(result);
  }, [navState.attach, liveRun, finished]);

  // Attach mode's HITL gate isn't known from any live HTTP response either —
  // reconstruct it from the durable checkpoint the same way a fresh page
  // load must (see GET .../pending-gate). A "user_requested" pause has no
  // gate to review — Run History's own pause/resume buttons already cover
  // that case — so this only ever populates `gate` for a real HITL node.
  const [gateFetchError, setGateFetchError] = useState<string | null>(null);
  const [gateRetryToken, setGateRetryToken] = useState(0);
  useEffect(() => {
    if (!navState.attach || !runId || finished || gate) return;
    if (liveRun?.status !== 'paused' || liveRun.pause_kind === 'user_requested') return;
    let cancelled = false;
    api.pendingGate(runId)
      .then((res) => {
        if (cancelled) return;
        if (!res.paused || res.pause_kind !== 'hitl_gate') return;
        setGateFetchError(null);
        setGate({
          nodeId: res.node_id,
          context: res.context,
          question: res.question,
          allowedActions: res.allowed_actions,
          content: res.content,
          allowDocumentOverride: res.allow_document_override,
          maxEditChars: res.max_edit_chars,
        });
      })
      .catch((e) => {
        if (!cancelled) setGateFetchError(String(e.message ?? e));
      });
    return () => { cancelled = true; };
  }, [navState.attach, runId, finished, gate, liveRun?.status, liveRun?.pause_kind, gateRetryToken]);

  // Derive node colors from SSE events (best-effort animation).
  const cockpit = useMemo(() => {
    const nodeIds = parsedWf?.nodes.map((n) => n.id) ?? [];
    return deriveCockpitState(nodeIds, events, streamOpen);
  }, [parsedWf, events, streamOpen]);

  // Build and arrange the graph once for this workflow.
  useEffect(() => {
    if (!parsedWf) return;
    const base = yamlToReactFlow(parsedWf);
    const initialNodes = base.nodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        status: 'pending' as NodeStatus,
      },
    }));
    setNodes(layoutFlow(initialNodes, base.edges));
    setEdges(base.edges);
  }, [parsedWf, setEdges, setNodes]);

  // SSE events change status without resetting the arranged positions.
  // Attach mode may reopen a run whose SSE history has already been evicted
  // from the event bus (it's a bounded in-memory replay buffer) — fall back
  // to the polled run detail so the graph still colors correctly.
  useEffect(() => {
    setNodes((current) =>
      current.map((node) => {
        const ssStatus = cockpit.nodeStates[node.data.nodeId];
        const status = ssStatus && ssStatus !== 'pending'
          ? ssStatus
          : liveRunNodeStatus(node.data.nodeId, liveRun) ?? ssStatus ?? 'pending';
        return { ...node, data: { ...node.data, status } };
      }),
    );
  }, [cockpit.nodeStates, liveRun, setNodes]);

  const activeNodeId = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (
        event.type === 'node_started'
        && cockpit.nodeStates[event.node_id] === 'active'
      ) {
        return event.node_id;
      }
    }
    return liveRun?.active_nodes?.[0] ?? null;
  }, [cockpit.nodeStates, events, liveRun]);
  const reusedNodeCount = useMemo(
    () => events.filter((event) => event.type === 'node_reused').length,
    [events],
  );

  const focusNode = useCallback((nodeId: string | null) => {
    if (!nodeId || !rfInstance) return;
    const node = nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;
    setSelectedId(nodeId);
    rfInstance.setCenter(
      node.position.x + (node.width ?? 260) / 2,
      node.position.y + (node.height ?? 92) / 2,
      { zoom: 1.15, duration: 450 },
    );
  }, [nodes, rfInstance]);

  useEffect(() => {
    if (!followRunning || !activeNodeId) return;
    requestAnimationFrame(() => focusNode(activeNodeId));
  }, [activeNodeId, focusNode, followRunning]);

  const showAllNodes = useCallback(() => {
    rfInstance?.fitView({ padding: 0.2, duration: 400 });
  }, [rfInstance]);

  const reorganizeNodes = useCallback(() => {
    setNodes((current) => layoutFlow(current, edges));
    requestAnimationFrame(showAllNodes);
  }, [edges, setNodes, showAllNodes]);

  // Pipeline mode: keep the pipeline's own gate/advance state alongside this
  // stage's run so the banner can offer "Continue to next stage" as soon as
  // this stage finishes. Re-fetched (not read off the trigger response) so it
  // stays correct even when this stage finished via a mid-run HITL resume,
  // which doesn't return the pipeline doc itself.
  const [pipelineDoc, setPipelineDoc] = useState<PipelineRunDetail | null>(null);
  const [continuingStage, setContinuingStage] = useState(false);
  const [continueError, setContinueError] = useState<string | null>(null);
  const pipelineRunId = navState.pipeline?.pipelineRunId;
  useEffect(() => {
    if (!pipelineRunId) return;
    let cancelled = false;
    api.pipelineRunDetail(pipelineRunId)
      .then((doc) => { if (!cancelled) setPipelineDoc(doc); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [pipelineRunId, finished]);

  const continueToNextStage = useCallback(async () => {
    if (!pipelineDoc) return;
    const nextIndex = pipelineDoc.current_stage_index + 1;
    const nextStage = pipelineDoc.stages[nextIndex];
    if (!nextStage) return;
    setContinuingStage(true);
    setContinueError(null);
    try {
      const { yaml: stageYaml } = await api.getWorkflow(nextStage.workflow);
      const stageRunId = crypto.randomUUID();
      navigate(`/cockpit/${stageRunId}`, {
        state: {
          workflowYaml: stageYaml,
          workflowName: nextStage.id,
          pipeline: {
            mode: 'advance',
            pipelineRunId: pipelineDoc.pipeline_run_id,
            pipelineName: pipelineDoc.pipeline_name,
            stageId: nextStage.id,
            stageIndex: nextIndex,
            totalStages: pipelineDoc.stages.length,
          },
        },
      });
    } catch (e: unknown) {
      setContinueError(e instanceof Error ? e.message : String(e));
      setContinuingStage(false);
    }
  }, [pipelineDoc, navigate]);

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
        <Spinner label="Parsing workflow…" />
      </div>
    );
  }

  const selectedNode = selectedId
    ? nodes.find((n) => n.id === selectedId) ?? null
    : null;
  const showHITL = gate !== null && finished === null;
  const showGateError = (
    navState.attach
    && !showHITL
    && !finished
    && liveRun?.status === 'paused'
    && liveRun.pause_kind !== 'user_requested'
    && gateFetchError !== null
  );
  const displayStatus = finished?.status ?? (gate ? 'paused' : cockpit.runStatus);

  return (
    <div className="h-full flex flex-col">
      {pipelineBanner}
      <div className="flex-1 flex min-h-0">
      <div className="flex-1 relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onInit={setRfInstance}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, n) => {
            setSelectedId(n.id);
            setSidebarTab('node');
          }}
          onPaneClick={() => {
            setSelectedId(null);
            setSidebarTab('variables');
          }}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          edgesUpdatable={false}
        >
          <Background gap={20} />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>

        {/* Header badge */}
        <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-md px-3 py-2 shadow-sm border border-slate-200">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{navState.workflowName ?? parsedWf.name}</span>
            <span
              className={`text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 ${STATUS_BADGE[displayStatus]}`}
            >
              {displayStatus}
            </span>
          </div>
          <div className="text-xs text-ink-500 mt-1 font-mono">run {runId.slice(0, 8)}…</div>
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
              live SSE feed reconnecting (gates still work)
            </div>
          )}
          {triggerError && <div className="text-xs text-bad mt-1">{triggerError}</div>}
        </div>

        <div className="absolute top-4 right-4 flex items-center gap-2">
          <button
            onClick={() => setFollowRunning((value) => !value)}
            className={`px-3 py-2 rounded-md border text-sm ${
              followRunning
                ? 'border-accent-600 bg-accent-50 text-accent-700'
                : 'border-slate-300 bg-white text-ink-700'
            }`}
          >
            Follow running {followRunning ? 'on' : 'off'}
          </button>
          <button
            onClick={() => focusNode(activeNodeId)}
            disabled={!activeNodeId}
            className="px-3 py-2 rounded-md border border-slate-300 bg-white text-sm hover:bg-slate-50 disabled:opacity-40"
          >
            Focus running
          </button>
          <button
            onClick={reorganizeNodes}
            className="px-3 py-2 rounded-md border border-slate-300 bg-white text-sm hover:bg-slate-50"
          >
            Reorganize
          </button>
          <button
            onClick={showAllNodes}
            className="px-3 py-2 rounded-md border border-slate-300 bg-white text-sm hover:bg-slate-50"
          >
            Show all
          </button>
        </div>
      </div>

      <aside
        className={`border-l border-slate-200 bg-white overflow-y-auto ${
          showHITL ? 'w-[min(760px,52vw)]' : 'w-96'
        }`}
      >
        {showHITL ? (
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
          />
        ) : showGateError ? (
          <div className="p-6">
            <div className="text-bad font-medium">Couldn't load this run's review gate</div>
            <div className="text-sm text-ink-500 mt-1">{gateFetchError}</div>
            <button
              onClick={() => {
                setGateFetchError(null);
                setGateRetryToken((value) => value + 1);
              }}
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
                  onClick={() => navigate(`/history/${runId}`)}
                  className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500"
                >
                  Open Run History and retry
                </button>
              </>
            )}
          </div>
        ) : (
          <div>
            <div className="sticky top-0 z-10 flex border-b border-slate-200 bg-white">
              <button
                type="button"
                onClick={() => setSidebarTab('variables')}
                className={`flex-1 px-3 py-2.5 text-xs ${
                  sidebarTab === 'variables'
                    ? 'border-b-2 border-accent-600 font-medium text-ink-900'
                    : 'text-ink-500'
                }`}
              >
                Variables
              </button>
              <button
                type="button"
                onClick={() => setSidebarTab('node')}
                className={`flex-1 px-3 py-2.5 text-xs ${
                  sidebarTab === 'node'
                    ? 'border-b-2 border-accent-600 font-medium text-ink-900'
                    : 'text-ink-500'
                }`}
              >
                Selected node
              </button>
            </div>

            {sidebarTab === 'variables' ? (
              <WorkflowVariablesPanel
                live
                inputs={liveRun?.inputs ?? navState.inputs ?? {}}
                variables={
                  liveRun?.variables
                  ?? Object.fromEntries(
                    (parsedWf.static_variables ?? [])
                      .map((item) => [item.name, item.value]),
                  )
                }
                outputs={liveRun?.outputs ?? {}}
              />
            ) : selectedNode === null ? (
              <div className="p-6 text-ink-500 text-sm">
                Click a node to see its output preview.
              </div>
            ) : (
              <div className="p-6">
                <div className="text-xs uppercase tracking-wide text-ink-500">{selectedNode.data.typeName}</div>
                <div className="font-semibold text-lg mt-1">{selectedNode.data.nodeId}</div>
                <div className="mt-2 text-xs text-ink-500">Status: {selectedNode.data.status}</div>

                {(() => {
                  const nodeOutput = liveRun?.outputs?.[selectedNode.data.nodeId]
                    ?? liveRun?.node_runs?.[selectedNode.data.nodeId]?.output;
                  const key = fileKey(nodeOutput);
                  if (!key) return null;
                  return (
                    <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 px-3 py-2">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-accent-800">
                          {artifactLabel(nodeOutput, key)}
                        </div>
                        <div className="text-[11px] text-ink-500 truncate font-mono">
                          {key.split('/').pop()}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => void api.downloadArtifact(key)}
                        className="flex-none px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500"
                      >
                        Download
                      </button>
                    </div>
                  );
                })()}

                <div className="flex items-center justify-between mt-6 mb-2">
                  <h3 className="text-sm font-medium text-ink-700">Output preview</h3>
                  {cockpit.outputPreviews[selectedNode.data.nodeId] && (
                    <CopyButton text={cockpit.outputPreviews[selectedNode.data.nodeId]} />
                  )}
                </div>
                {cockpit.outputPreviews[selectedNode.data.nodeId] ? (
                  <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
{cockpit.outputPreviews[selectedNode.data.nodeId]}
                  </pre>
                ) : (
                  <div className="text-sm text-ink-500">
                    {selectedNode.data.status === 'pending' && 'Waiting to start.'}
                    {selectedNode.data.status === 'active' && 'Running…'}
                    {selectedNode.data.status === 'reused' && 'Reused without a provider call.'}
                    {selectedNode.data.status === 'paused' && 'Paused for human approval.'}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </aside>
      </div>
    </div>
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
  pipeline: PipelineNavState;
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
