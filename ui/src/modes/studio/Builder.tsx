import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node as RFNode,
  type Edge as RFEdge,
  type ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { api } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import type { NodeTypeManifest } from '../../api/types';
import { WorkflowNode } from './WorkflowNode';
import { NodePalette } from './NodePalette';
import { ConfigPanel } from './ConfigPanel';
import {
  parseYaml,
  dumpYaml,
  yamlToReactFlow,
  reactFlowToYaml,
  type WorkflowNodeData,
  type YamlWorkflow,
} from './yaml-bridge';
import { generateDefaults, newNodeId, findManifest } from './builder-helpers';

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
      setNodes(ns);
      setEdges(es);
      const { nodes: _n, edges: _e, ...rest } = wf;
      setMeta(rest);
    });
  }, [name, setNodes, setEdges]);

  // ---- Edit handlers ----

  const onConnect = useCallback(
    (c: Connection) => setEdges(eds => addEdge(c, eds)),
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
    } catch (e: any) {
      setSaveState('error');
      setSaveError(String(e.message ?? e));
    }
  }, [meta, name, nodes, edges]);

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
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          fitView
          deleteKeyCode={['Backspace', 'Delete']}
        >
          <Background gap={20} />
          <Controls />
        </ReactFlow>

        {/* Top-left badge */}
        <div className="absolute top-4 left-4 bg-white/90 backdrop-blur rounded-md px-3 py-2 shadow-sm border border-slate-200">
          <div className="text-sm font-medium">{meta.name}</div>
          <div className="text-xs text-ink-500">{nodes.length} nodes</div>
        </div>

        {/* Top-right save */}
        <div className="absolute top-4 right-4 flex items-center gap-3">
          {saveState === 'saved' && <span className="text-xs text-ok">Saved</span>}
          {saveState === 'error' && (
            <span className="text-xs text-bad max-w-xs truncate" title={saveError ?? ''}>
              Save failed
            </span>
          )}
          <button
            onClick={onSave}
            disabled={saveState === 'saving'}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {saveState === 'saving' ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      <aside className="w-96 border-l border-slate-200 bg-white">
        <ConfigPanel
          selected={selected}
          manifests={manifests}
          onIdChange={onIdChange}
          onConfigChange={onConfigChange}
        />
      </aside>
    </div>
  );
}