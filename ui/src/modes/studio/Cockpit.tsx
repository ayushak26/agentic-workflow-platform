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
import { useRunSocket } from '../../hooks/useRunSocket';
import { Spinner } from '../../components/Spinner';
import { CockpitNode } from './CockpitNode';
import { HITLPanel } from './HITLPanel';
import { OutputViewer } from './OutputViewer';
import { WorkflowVariablesPanel } from './WorkflowVariablesPanel';
import { parseYaml, yamlToReactFlow, type WorkflowNodeData, type YamlWorkflow } from './yaml-bridge';
import { deriveCockpitState, type NodeStatus } from './cockpit-state';
import { useSetRunCost } from "../../RunCostContext";
import { layoutFlow } from './flow-layout';
import type {
  HITLReviewContent,
  ModelSelection,
  RunDetail,
} from '../../api/types';

type CockpitNodeData = WorkflowNodeData & {
  status: NodeStatus;
  modelSelection?: ModelSelection;
};
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

export function Cockpit() {
  const { runId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  // Snapshot navigation state ONCE so the component binds to a stable run for its
  // whole life — re-deriving it each render could remount and kill the socket.
  const [navState] = useState(
    () =>
      (location.state ?? {}) as {
        workflowYaml?: string;
        inputs?: Record<string, unknown>;
        workflowName?: string;
        retrySourceRunId?: string;
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
  // NOT the WebSocket — so approvals work even if the socket drops mid-run.
  const [gate, setGate] = useState<Gate | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const setRunCost = useSetRunCost();

  const { events, open: wsOpen, error: wsError } = useRunSocket(runId ?? null);

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
// which path (HTTP resume vs WS) marked it finished.
useEffect(() => {
  if (finished?.status === 'completed' && runId) {
    api.costForRun(runId)
      .then(c => setRunCost(c.total_usd))
      .catch(e => console.error('cost fetch failed', e));
  }
}, [finished, runId, setRunCost]);
  // After WS opens, trigger the run exactly once. The run response seeds the first gate.
  useEffect(() => {
    if (!wsOpen || runTriggered || !navState.workflowYaml || !runId) return;
    // This state guards the one external run request owned by this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRunTriggered(true);
    const request = navState.retrySourceRunId
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
      .then((res) => applyResumeResult(res))
      .catch((e) => {
        const message = String(e.message ?? e);
        setTriggerError(message);
        setFinished({ status: 'failed', error: message });
      });
  }, [
    wsOpen,
    runTriggered,
    navState.workflowYaml,
    navState.inputs,
    navState.retrySourceRunId,
    runId,
  ]);

  // Exact inputs and completed outputs are persisted incrementally in run
  // history. Polling that record powers the Variables panel while the graph is
  // still executing; WebSocket previews remain intentionally small.
  useEffect(() => {
    if (!runId || !runTriggered || finished) return;
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
  }, [finished, runId, runTriggered]);

  // Derive node colors from WS events (best-effort animation).
  const cockpit = useMemo(() => {
    const nodeIds = parsedWf?.nodes.map((n) => n.id) ?? [];
    return deriveCockpitState(nodeIds, events, wsOpen);
  }, [parsedWf, events, wsOpen]);

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

  // WebSocket events change status without resetting the arranged positions.
  useEffect(() => {
    setNodes((current) =>
      current.map((node) => ({
        ...node,
        data: {
          ...node.data,
          status: cockpit.nodeStates[node.data.nodeId] ?? 'pending',
          modelSelection: (
            cockpit.modelSelections[node.data.nodeId] ?? []
          ).at(-1),
        },
      })),
    );
  }, [cockpit.modelSelections, cockpit.nodeStates, setNodes]);

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
    return null;
  }, [cockpit.nodeStates, events]);
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

  // ✅ early returns go AFTER all hooks
  if (finished?.status === 'completed') {
    return (
      <OutputViewer
        runId={runId}
        state={finished.state}
        projectedOutput={finished.output}
        workflowName={navState.workflowName ?? parsedWf?.name}
      />
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
  const displayStatus = finished?.status ?? (gate ? 'paused' : cockpit.runStatus);

  return (
    <div className="h-full flex">
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
          {wsError && <div className="text-xs text-ink-500 mt-1">live feed offline (gates still work)</div>}
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

                <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">
                  LLM selection
                </h3>
                {selectedNode.data.modelSelection ? (
                  <div className="rounded-md border border-accent-200 bg-accent-50 p-3 text-xs">
                    <div className="font-semibold text-accent-800">
                      {selectedNode.data.modelSelection.actual_model}
                    </div>
                    <div className="text-ink-500 mt-1">
                      Requested: {selectedNode.data.modelSelection.requested_model}
                      {' · '}
                      {selectedNode.data.modelSelection.mode}
                      {' · '}
                      {selectedNode.data.modelSelection.complexity}
                      {' '}
                      {selectedNode.data.modelSelection.task_kind.replace('_', ' ')}
                    </div>
                    <div className="text-ink-700 mt-2">
                      {selectedNode.data.modelSelection.reason}
                    </div>
                    <div className="text-ink-500 mt-2">
                      Estimated maximum call cost: $
                      {selectedNode.data.modelSelection.estimated_cost_usd.toFixed(6)}
                      {selectedNode.data.modelSelection.fallback
                        ? ' · provider fallback'
                        : ''}
                    </div>
                  </div>
                ) : (
                  <div className="text-sm text-ink-500">
                    No LLM provider call recorded for this node yet.
                  </div>
                )}

                <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">Output preview</h3>
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
  );
}
