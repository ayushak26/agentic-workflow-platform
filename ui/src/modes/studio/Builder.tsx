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
  LLMModelInfo,
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
  type ModelRoutingPolicy,
  type YamlWorkflow,
} from './yaml-bridge';
import { generateDefaults, newNodeId, findManifest } from './builder-helpers';
import { layoutFlow } from './flow-layout';
import { WorkflowInputsPanel } from './WorkflowInputsPanel';
import { Icon } from '../../components/ui/Icon';

const nodeTypes = { workflow: WorkflowNode };

export function Builder() {
  const { name } = useParams();
  const [meta, setMeta] = useState<Omit<YamlWorkflow, 'nodes' | 'edges'> | null>(null);
  const [manifests, setManifests] = useState<NodeTypeManifest[]>([]);
  const [llmModels, setLlmModels] = useState<LLMModelInfo[]>([]);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showInputs, setShowInputs] = useState(false);
  const [validating, setValidating] = useState(false);
  const [preflight, setPreflight] = useState<WorkflowPreflightReport | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(() => window.innerWidth > 900);
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 900);

  // Load node-type manifests once (used by palette + config form).
  useEffect(() => {
    api.nodeTypes().then(setManifests).catch(console.error);
    api.llmModels()
      .then(({ models }) => setLlmModels(models))
      .catch(console.error);
  }, []);

  // Load workflow YAML, hydrate state.
  useEffect(() => {
    if (!name) return;
    api.getWorkflow(name).then(({ yaml }) => {
      const wf = parseYaml(yaml);
      const { nodes: ns, edges: es } = yamlToReactFlow(wf);
      setNodes(layoutFlow(ns, es, 'TB').nodes);
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
      const supportsModelSelection = Boolean(
        (
          manifest.config_schema as {
            properties?: Record<string, unknown>;
          }
        ).properties?.model,
      );

      const newNode: RFNode<WorkflowNodeData> = {
        id,
        type: 'workflow',
        position,
        data: {
          nodeId: id,
          typeName,
          config,
          ...(supportsModelSelection
            ? {
                selectedModel: 'auto',
                modelRouting: {
                  accuracy_priority: 'maximum' as const,
                  prefer_low_latency: false,
                },
              }
            : {}),
        },
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

  const onModelSelectionChange = useCallback(
    (nextModel: string | null) => {
      if (!selectedId) return;
      setPreflight(null);
      setNodes(current =>
        current.map(node =>
          node.id === selectedId
            ? {
                ...node,
                data: {
                  ...node.data,
                  selectedModel: nextModel,
                },
              }
            : node,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const onModelRoutingChange = useCallback(
    (nextPolicy: ModelRoutingPolicy | undefined) => {
      if (!selectedId) return;
      setPreflight(null);
      setNodes(current =>
        current.map(node =>
          node.id === selectedId
            ? {
                ...node,
                data: {
                  ...node.data,
                  modelRouting: nextPolicy,
                },
              }
            : node,
        ),
      );
    },
    [selectedId, setNodes],
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
    setNodes((current) => layoutFlow(current, edges, 'TB').nodes);
    requestAnimationFrame(showAllNodes);
  }, [edges, setNodes, showAllNodes]);

  if (!name) {
    return <div className="p-8 text-ink-500">No workflow selected. Pick one from the Library.</div>;
  }
  if (!meta) {
    return <div className="p-8"><Spinner label="Loading workflow…" /></div>;
  }

  return (
    <div className="builder-shell flex h-full">
      {paletteOpen && <aside className="builder-palette">
        <NodePalette />
      </aside>}

      <div className="builder-canvas relative flex-1" onDrop={onDrop} onDragOver={onDragOver}>
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
            setInspectorOpen(true);
            if (window.innerWidth <= 900) setPaletteOpen(false);
          }}
          onPaneClick={() => setSelectedId(null)}
          fitView
          deleteKeyCode={['Backspace', 'Delete']}
        >
          <Background color="var(--border-default)" gap={20} />
          <Controls />
          <MiniMap maskColor="rgba(242, 251, 250, 0.72)" nodeColor="var(--brand-teal-600)" pannable zoomable />
        </ReactFlow>

        {/* Top-left badge */}
        <div className="absolute left-3 top-3 z-10 max-w-[240px] rounded-md border border-ink-200 bg-white/95 px-3 py-2 shadow-sm backdrop-blur">
          <div className="truncate text-sm font-semibold text-ink-950" title={meta.name}>{meta.name}</div>
          <div className="text-xs text-ink-500">{nodes.length} nodes</div>
        </div>

        {/* Top-right canvas actions + save */}
        <div className="canvas-toolbar absolute right-3 top-3 z-20">
          {saveState === 'saved' && <span className="text-xs text-ok">Saved</span>}
          {saveState === 'error' && (
            <span className="text-xs text-bad max-w-xs truncate" title={saveError ?? ''}>
              Save failed
            </span>
          )}
          <button
            aria-pressed={paletteOpen}
            className={`ui-button min-h-8 px-2.5 ${paletteOpen ? 'border-accent-500 bg-accent-50 text-accent-700' : 'ui-button--secondary'}`}
            onClick={() => setPaletteOpen(value => !value)}
            title={paletteOpen ? 'Hide node library' : 'Show node library'}
            type="button"
          >
            <Icon name="layout" size={15} />
            Nodes
          </button>
          <button
            onClick={() => {
              setSelectedId(null);
              setShowInputs(true);
              setInspectorOpen(true);
              if (window.innerWidth <= 900) setPaletteOpen(false);
            }}
            className={`ui-button min-h-8 px-2.5 ${
              showInputs
                ? 'border-accent-600 bg-accent-50 text-accent-600'
                : 'ui-button--secondary'
            }`}
          >
            Inputs ({Object.keys(meta.inputs ?? {}).length})
          </button>
          <button
            onClick={reorganizeNodes}
            className="ui-button ui-button--secondary min-h-8 px-2.5"
            title="Arrange nodes into stages"
          >
            Reorganize
          </button>
          <button
            onClick={showAllNodes}
            className="ui-button ui-button--secondary min-h-8 px-2.5"
            title="Fit all nodes on screen"
          >
            Show all nodes
          </button>
          <button
            aria-pressed={inspectorOpen}
            className={`ui-button min-h-8 px-2.5 ${inspectorOpen ? 'border-accent-500 bg-accent-50 text-accent-700' : 'ui-button--secondary'}`}
            onClick={() => setInspectorOpen(value => !value)}
            title={inspectorOpen ? 'Hide configuration panel' : 'Show configuration panel'}
            type="button"
          >
            Inspector
          </button>
          <button
            onClick={onValidate}
            disabled={validating}
            className="ui-button ui-button--secondary min-h-8 px-2.5"
          >
            {validating ? 'Checking…' : 'Preflight'}
          </button>
          <button
            onClick={onSave}
            disabled={saveState === 'saving'}
            className="ui-button ui-button--primary min-h-8 px-3"
          >
            <Icon name="save" size={15} />
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

      {inspectorOpen && <aside className="builder-inspector">
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
            llmModels={llmModels}
            onIdChange={onIdChange}
            onConfigChange={onConfigChange}
            onModelSelectionChange={onModelSelectionChange}
            onModelRoutingChange={onModelRoutingChange}
          />
        )}
      </aside>}
    </div>
  );
}
