import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node as RFNode,
  type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { api } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import type {
  NodeTypeManifest,
  WorkflowPreflightReport,
} from '../../api/types';
import { WorkflowNode } from './WorkflowNode';
import { NodePalette } from './NodePalette';
import { ConfigPanel } from './ConfigPanel';
import {
  parseYaml,
  dumpYaml,
  yamlToReactFlow,
  reactFlowToYaml,
  type WorkflowNodeData,
  type WorkflowInputSpec,
  type YamlWorkflow,
} from './yaml-bridge';
import { generateDefaults, newNodeId, findManifest } from './builder-helpers';
import { layoutFlow } from './flow-layout';
import { WorkflowInputsPanel } from './WorkflowInputsPanel';

const nodeTypes = { workflow: WorkflowNode };

export function Builder() {
  const { name } = useParams();
  const [meta, setMeta] = useState<Omit<YamlWorkflow, 'nodes' | 'edges'> | null>(null);
  const [manifests, setManifests] = useState<NodeTypeManifest[]>([]);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showInputs, setShowInputs] = useState(false);
  const [validating, setValidating] = useState(false);
  const [preflight, setPreflight] = useState<WorkflowPreflightReport | null>(null);

  // Load node-type manifests once (used by palette + config form).
  useEffect(() => {
    api.nodeTypes().then(setManifests).catch(console.error);
  }, []);

  // Load workflow YAML, hydrate state.
  useEffect(() => {
    if (!name) return;
    api.getWorkflow(name).then(({ yaml }) => {
      const wf = parseYaml(yaml);
      const { nodes: ns, edges: es } = yamlToReactFlow(wf);
      setNodes(layoutFlow(ns, es));
      setEdges(es);
      const { nodes: workflowNodes, edges: workflowEdges, ...rest } = wf;
      void workflowNodes;
      void workflowEdges;
      setMeta(rest);
    });
  }, [name, setNodes, setEdges]);

  // ---- Edit handlers ----

  const onConnect = useCallback(
    (c: Connection) => {
      setPreflight(null);
      setEdges(eds => addEdge(c, eds));
    },
    [setEdges],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const typeName = event.dataTransfer.getData('application/reactflow');
      if (!typeName || !rfInstance) return;

      const manifest = findManifest(manifests, typeName);
      if (!manifest) return;

      const position = rfInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      const existingIds = new Set(nodes.map(n => n.data.nodeId));
      const id = newNodeId(typeName, existingIds);
      const config = generateDefaults(manifest.config_schema) ?? {};

      const newNode: RFNode<WorkflowNodeData> = {
        id,
        type: 'workflow',
        position,
        data: { nodeId: id, typeName, config },
      };
      setPreflight(null);
      setNodes(ns => [...ns, newNode]);
    },
    [rfInstance, manifests, nodes, setNodes],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // ---- Config edits in the side panel ----

  const selected = useMemo(
    () => (selectedId ? nodes.find(n => n.id === selectedId) ?? null : null),
    [selectedId, nodes],
  );

  const onConfigChange = useCallback(
    (nextConfig: Record<string, unknown>) => {
      if (!selectedId) return;
      setPreflight(null);
      setNodes(ns =>
        ns.map(n =>
          n.id === selectedId
            ? { ...n, data: { ...n.data, config: nextConfig } }
            : n,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const onIdChange = useCallback(
    (nextId: string) => {
      if (!selectedId) return;
      setPreflight(null);
      setNodes(ns =>
        ns.map(n =>
          n.id === selectedId
            ? { ...n, id: nextId, data: { ...n.data, nodeId: nextId } }
            : n,
        ),
      );
      // Repoint any edges that referenced the old id.
      setEdges(es =>
        es.map(e => ({
          ...e,
          source: e.source === selectedId ? nextId : e.source,
          target: e.target === selectedId ? nextId : e.target,
        })),
      );
      setSelectedId(nextId);
    },
    [selectedId, setNodes, setEdges],
  );

  // ---- Save ----

  const onSave = useCallback(async () => {
    if (!meta || !name) return;
    setSaveState('saving');
    setSaveError(null);
    try {
      const wf = reactFlowToYaml(meta, nodes, edges);
      const text = dumpYaml(wf);
      await api.saveWorkflow(name, text);
      setSaveState('saved');
      setTimeout(() => setSaveState('idle'), 1500);
    } catch (e: unknown) {
      setSaveState('error');
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }, [meta, name, nodes, edges]);

  const onValidate = useCallback(async () => {
    if (!meta) return;
    setValidating(true);
    setSaveError(null);
    try {
      const workflow = reactFlowToYaml(meta, nodes, edges);
      const report = await api.validateWorkflow(dumpYaml(workflow));
      setPreflight(report);
    } catch (error: unknown) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setValidating(false);
    }
  }, [meta, nodes, edges]);

  const showAllNodes = useCallback(() => {
    rfInstance?.fitView({ padding: 0.2, duration: 400 });
  }, [rfInstance]);

  const reorganizeNodes = useCallback(() => {
    setNodes((current) => layoutFlow(current, edges));
    requestAnimationFrame(showAllNodes);
  }, [edges, setNodes, showAllNodes]);

  if (!name) {
    return <div className="p-8 text-ink-500">No workflow selected. Pick one from the Library.</div>;
  }
  if (!meta) {
    return <div className="p-8"><Spinner label="Loading workflow…" /></div>;
  }

  return (
    <div className="h-full flex">
      <aside className="w-56 border-r border-slate-200 bg-slate-50 overflow-y-auto">
        <NodePalette />
      </aside>

      <div className="flex-1 relative" onDrop={onDrop} onDragOver={onDragOver}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onInit={setRfInstance}
          onNodeClick={(_, n) => {
            setSelectedId(n.id);
            setShowInputs(false);
          }}
          onPaneClick={() => setSelectedId(null)}
          fitView
          deleteKeyCode={['Backspace', 'Delete']}
        >
          <Background gap={20} />
          <Controls />
          <MiniMap pannable zoomable />
        </ReactFlow>

        {/* Top-left badge */}
        <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-md px-3 py-2 shadow-sm border border-slate-200">
          <div className="text-sm font-medium">{meta.name}</div>
          <div className="text-xs text-ink-500">{nodes.length} nodes</div>
        </div>

        {/* Top-right canvas actions + save */}
        <div className="absolute top-4 right-4 flex items-center gap-3">
          {saveState === 'saved' && <span className="text-xs text-ok">Saved</span>}
          {saveState === 'error' && (
            <span className="text-xs text-bad max-w-xs truncate" title={saveError ?? ''}>
              Save failed
            </span>
          )}
          <button
            onClick={() => {
              setSelectedId(null);
              setShowInputs(true);
            }}
            className={`px-3 py-2 rounded-md border text-sm ${
              showInputs
                ? 'border-accent-600 bg-accent-50 text-accent-600'
                : 'border-slate-300 bg-white hover:bg-slate-50'
            }`}
          >
            Inputs ({Object.keys(meta.inputs ?? {}).length})
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
            Show all nodes
          </button>
          <button
            onClick={onValidate}
            disabled={validating}
            className="px-3 py-2 rounded-md border border-slate-300 bg-white text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            {validating ? 'Checking…' : 'Preflight'}
          </button>
          <button
            onClick={onSave}
            disabled={saveState === 'saving'}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {saveState === 'saving' ? 'Saving…' : 'Save'}
          </button>
        </div>

        {preflight && (
          <div
            className={`absolute bottom-4 left-4 z-10 w-[min(620px,70%)] rounded-lg border bg-white p-4 shadow-lg ${
              preflight.valid ? 'border-emerald-300' : 'border-red-300'
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <div
                  className={`text-sm font-semibold ${
                    preflight.valid ? 'text-emerald-700' : 'text-red-700'
                  }`}
                >
                  {preflight.valid
                    ? 'Preflight passed'
                    : `Preflight blocked: ${
                        preflight.issues.filter(item => item.severity === 'error').length
                      } error(s)`}
                </div>
                <div className="mt-1 text-xs text-ink-500">
                  {preflight.node_count} nodes · {preflight.edge_count} edges ·{' '}
                  {preflight.tokens_spent} tokens used
                </div>
              </div>
              <button
                type="button"
                onClick={() => setPreflight(null)}
                className="text-lg leading-none text-ink-500 hover:text-ink-900"
              >
                ×
              </button>
            </div>
            {preflight.issues.length > 0 && (
              <ul className="mt-3 max-h-44 space-y-2 overflow-y-auto text-xs">
                {preflight.issues.slice(0, 8).map((issue, index) => (
                  <li
                    key={`${issue.code}:${issue.path ?? index}`}
                    className={
                      issue.severity === 'error'
                        ? 'text-red-700'
                        : 'text-amber-700'
                    }
                  >
                    <span className="font-semibold">{issue.code}</span>
                    {issue.node_id ? ` · ${issue.node_id}` : ''}
                    {issue.path ? ` · ${issue.path}` : ''}
                    {`: ${issue.message}`}
                    {issue.suggestion ? ` ${issue.suggestion}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <aside className="w-96 border-l border-slate-200 bg-white">
        {showInputs ? (
          <WorkflowInputsPanel
            inputs={meta.inputs ?? {}}
            onChange={(inputs: Record<string, WorkflowInputSpec>) => {
              setMeta(current => (
                current ? { ...current, inputs } : current
              ));
              setPreflight(null);
              setSaveState('idle');
            }}
            onClose={() => setShowInputs(false)}
          />
        ) : (
          <ConfigPanel
            selected={selected}
            manifests={manifests}
            onIdChange={onIdChange}
            onConfigChange={onConfigChange}
          />
        )}
      </aside>
    </div>
  );
}
