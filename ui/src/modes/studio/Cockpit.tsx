import { useEffect, useMemo, useState } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  type Node as RFNode,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { api } from '../../api/client';
import { useRunSocket } from '../../hooks/useRunSocket';
import { Spinner } from '../../components/Spinner';
import { CockpitNode } from './CockpitNode';
import { HITLPanel } from './HITLPanel';
import { parseYaml, yamlToReactFlow, type WorkflowNodeData, type YamlWorkflow } from './yaml-bridge';
import { deriveCockpitState, type NodeStatus } from './cockpit-state';

type CockpitNodeData = WorkflowNodeData & { status: NodeStatus };
const nodeTypes = { workflow: CockpitNode };

const STATUS_BADGE: Record<string, string> = {
  connecting: 'bg-slate-200 text-ink-700',
  running:    'bg-accent-600 text-white',
  paused:     'bg-warn text-white',
  completed:  'bg-ok text-white',
  failed:     'bg-bad text-white',
};

export function Cockpit() {
  const { runId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const navState = (location.state ?? {}) as {
    workflowYaml?: string;
    inputs?: Record<string, unknown>;
    workflowName?: string;
  };

  const [parsedWf, setParsedWf] = useState<YamlWorkflow | null>(null);
  const [runTriggered, setRunTriggered] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { events, open: wsOpen, error: wsError } = useRunSocket(runId ?? null);

  // Parse the YAML passed via navigation state.
  useEffect(() => {
    if (navState.workflowYaml) setParsedWf(parseYaml(navState.workflowYaml));
  }, [navState.workflowYaml]);

  // After WS opens, trigger the run. Exactly once.
  useEffect(() => {
    if (!wsOpen || runTriggered || !navState.workflowYaml || !runId) return;
    setRunTriggered(true);
    api.runWorkflow(navState.workflowYaml, navState.inputs ?? {}, undefined, runId)
      .catch(e => setTriggerError(String(e.message ?? e)));
  }, [wsOpen, runTriggered, navState.workflowYaml, navState.inputs, runId]);

  // Derive node states from events. Pure function, memoized.
  const cockpit = useMemo(() => {
    const nodeIds = parsedWf?.nodes.map(n => n.id) ?? [];
    return deriveCockpitState(nodeIds, events, wsOpen);
  }, [parsedWf, events, wsOpen]);

  // Build React Flow nodes with status injected into data.
  const { nodes, edges } = useMemo(() => {
    if (!parsedWf) return { nodes: [] as RFNode<CockpitNodeData>[], edges: [] };
    const base = yamlToReactFlow(parsedWf);
    const nodes = base.nodes.map<RFNode<CockpitNodeData>>(n => ({
      ...n,
      data: { ...n.data, status: cockpit.nodeStates[n.data.nodeId] ?? 'pending' },
    }));
    return { nodes, edges: base.edges };
  }, [parsedWf, cockpit.nodeStates]);

  if (!runId) {
    return <div className="p-8 text-ink-500">No run id in URL.</div>;
  }
  if (!navState.workflowYaml) {
    return (
      <div className="p-8">
        <div className="text-bad">No workflow YAML in navigation state.</div>
        <div className="text-ink-500 text-sm mt-2">
          Cockpits are launched from the Library's Run button. Direct navigation isn't supported yet (Phase 11 will add a snapshot endpoint that lets you reattach).
        </div>
        <button
          onClick={() => navigate('/studio/library')}
          className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm"
        >
          Back to Library
        </button>
      </div>
    );
  }
  if (!parsedWf) {
    return <div className="p-8"><Spinner label="Parsing workflow…" /></div>;
  }

  const selectedNode = selectedId ? nodes.find(n => n.id === selectedId) : null;
  const showHITL = cockpit.pausedNode !== null;

  return (
    <div className="h-full flex">
      <div className="flex-1 relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          edgesUpdatable={false}
        >
          <Background gap={20} />
          <Controls />
        </ReactFlow>

        {/* Header badge */}
        <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-md px-3 py-2 shadow-sm border border-slate-200">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{navState.workflowName ?? parsedWf.name}</span>
            <span className={`text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 ${STATUS_BADGE[cockpit.runStatus]}`}>
              {cockpit.runStatus}
            </span>
          </div>
          <div className="text-xs text-ink-500 mt-1 font-mono">run {runId.slice(0, 8)}…</div>
          {wsError && <div className="text-xs text-bad mt-1">{wsError}</div>}
          {triggerError && <div className="text-xs text-bad mt-1">{triggerError}</div>}
        </div>
      </div>

      <aside className="w-96 border-l border-slate-200 bg-white overflow-y-auto">
        {showHITL ? (
          <HITLPanel
            runId={runId}
            pausedNodeId={cockpit.pausedNode!.id}
            context={cockpit.pausedNode!.context}
          />
        ) : selectedNode === null ? (
          <div className="p-6 text-ink-500 text-sm">
            Click a node to see its output preview.
            {cockpit.runStatus === 'completed' && <div className="mt-3 text-ok">Run completed.</div>}
            {cockpit.runStatus === 'failed' && <div className="mt-3 text-bad">{cockpit.errorMessage}</div>}
          </div>
        ) : (
          <div className="p-6">
            <div className="text-xs uppercase tracking-wide text-ink-500">{selectedNode.data.typeName}</div>
            <div className="font-semibold text-lg mt-1">{selectedNode.data.nodeId}</div>
            <div className="mt-2 text-xs text-ink-500">Status: {selectedNode.data.status}</div>

            <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">Output preview</h3>
            {cockpit.outputPreviews[selectedNode.data.nodeId] ? (
              <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
{cockpit.outputPreviews[selectedNode.data.nodeId]}
              </pre>
            ) : (
              <div className="text-sm text-ink-500">
                {selectedNode.data.status === 'pending' && 'Waiting to start.'}
                {selectedNode.data.status === 'active' && 'Running…'}
                {selectedNode.data.status === 'paused' && 'Paused for human approval.'}
              </div>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}