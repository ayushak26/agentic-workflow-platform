import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
  type Viewport,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { api } from '../../api/client';
import type {
  LLMModelInfo,
  NodeTypeManifest,
  WorkflowDraft,
  WorkflowPreflightReport,
} from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { Icon } from '../../components/ui/Icon';
import { BuilderInspector, type BuilderInspectorTab } from './BuilderInspector';
import { BuilderStart } from './BuilderStart';
import { renameNodeReferencesInConfig } from './builder-graph';
import { generateDefaults, findManifest, newNodeId } from './builder-helpers';
import { layoutFlow } from './flow-layout';
import { NodePalette } from './NodePalette';
import { RunDialog } from './RunDialog';
import { SaveAsDialog } from './SaveAsDialog';
import { VersionHistoryPanel } from './VersionHistoryPanel';
import { WorkflowNode } from './WorkflowNode';
import {
  dumpYaml,
  parseYaml,
  reactFlowToYaml,
  yamlToReactFlow,
  type ModelRoutingPolicy,
  type NodeExperienceSpec,
  type WorkflowEdgeData,
  type WorkflowInputSpec,
  type WorkflowNodeData,
  type YamlWorkflow,
} from './yaml-bridge';

const nodeTypes = { workflow: WorkflowNode };
const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

type WorkflowMeta = Omit<YamlWorkflow, 'nodes' | 'edges'>;
type BuilderSnapshot = {
  meta: WorkflowMeta;
  nodes: Node<WorkflowNodeData>[];
  edges: Edge<WorkflowEdgeData>[];
  selectedId: string | null;
};
type RunDraft = {
  title: string;
  workflow: YamlWorkflow;
};
// Two independent sources deliver navigation state into a fresh Builder
// mount: GenerateWorkflowDialog ("Generate from prompt" → Open in Builder)
// and Cockpit's "Back to Builder" action. Both arrive via location.state.
type BuilderNavState = {
  generatedYaml?: string;
  builderResume?: {
    selectedNodeId?: string | null;
    viewport?: Viewport;
  };
};

function cloneSnapshot(snapshot: BuilderSnapshot): BuilderSnapshot {
  return structuredClone(snapshot);
}

function canvasFor(
  nodes: Node<WorkflowNodeData>[],
  selectedId: string | null,
  instance: ReactFlowInstance | null,
): WorkflowDraft['canvas'] {
  return {
    nodes: nodes.map(node => ({ id: node.id, position: node.position })),
    viewport: instance?.getViewport(),
    selected_node_id: selectedId,
  };
}

function applyCanvasPositions(
  nodes: Node<WorkflowNodeData>[],
  canvas?: WorkflowDraft['canvas'],
): Node<WorkflowNodeData>[] | null {
  const positions = new Map(
    (canvas?.nodes ?? []).map(item => [item.id, item.position]),
  );
  if (positions.size === 0 || !nodes.every(node => positions.has(node.id))) return null;
  return nodes.map(node => ({ ...node, position: positions.get(node.id)! }));
}

function relatedPath(selectedId: string | null, edges: Edge[]): Set<string> {
  if (!selectedId) return new Set();
  const related = new Set<string>([selectedId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of edges) {
      if (related.has(edge.source) && !related.has(edge.target)) {
        related.add(edge.target);
        changed = true;
      }
      if (related.has(edge.target) && !related.has(edge.source)) {
        related.add(edge.source);
        changed = true;
      }
    }
  }
  return related;
}

export function Builder() {
  const { name: routeName } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [navState] = useState(() => (location.state ?? {}) as BuilderNavState);
  const [meta, setMeta] = useState<WorkflowMeta | null>(null);
  // The workflow's file slug — distinct from meta.name, which is the
  // free-text display title. Stays null until a brand-new, generated-from-
  // prompt workflow is named via Save As.
  const [workflowName, setWorkflowName] = useState<string | null>(routeName ?? null);
  const [showSaveAs, setShowSaveAs] = useState(false);
  const [manifests, setManifests] = useState<NodeTypeManifest[]>([]);
  const [llmModels, setLlmModels] = useState<LLMModelInfo[]>([]);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, applyNodeChanges] = useNodesState<WorkflowNodeData>([]);
  const [edges, setEdges, applyEdgeChanges] = useEdgesState<WorkflowEdgeData>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(() => window.innerWidth > 1020);
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 1020);
  const [inspectorTab, setInspectorTab] = useState<BuilderInspectorTab>('configure');
  const [showInputs, setShowInputs] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [autosaveState, setAutosaveState] = useState<'idle' | 'saving' | 'saved' | 'error' | 'recovered'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  const [preflight, setPreflight] = useState<WorkflowPreflightReport | null>(null);
  const [autofixing, setAutofixing] = useState(false);
  const [recovery, setRecovery] = useState<WorkflowDraft | null>(null);
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [runDraft, setRunDraft] = useState<RunDraft | null>(null);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [past, setPast] = useState<BuilderSnapshot[]>([]);
  const [future, setFuture] = useState<BuilderSnapshot[]>([]);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragStart = useRef<BuilderSnapshot | null>(null);
  const pendingViewport = useRef<Viewport | null>(navState.builderResume?.viewport ?? null);
  // Set right before navigate() in startBlank/startTemplate: the route-load
  // effect below would otherwise immediately re-fetch the name we just
  // hydrated in memory, 404 (nothing is saved yet), fail the draft fetch
  // too (autosave hasn't fired within its debounce yet), and surface a
  // spurious "workflow not found" error over content that is actually fine.
  const skipNextRouteLoad = useRef<string | null>(null);

  useEffect(() => {
    Promise.all([api.nodeTypes(), api.llmModels()])
      .then(([types, modelResult]) => {
        setManifests(types);
        setLlmModels(modelResult.models);
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  const currentWorkflow = useMemo(
    () => meta ? reactFlowToYaml(meta, nodes, edges) : null,
    [edges, meta, nodes],
  );
  const currentYaml = useMemo(
    () => currentWorkflow ? dumpYaml(currentWorkflow) : '',
    [currentWorkflow],
  );
  const selected = useMemo(
    () => selectedId ? nodes.find(node => node.id === selectedId) ?? null : null,
    [nodes, selectedId],
  );

  const captureSnapshot = useCallback((): BuilderSnapshot | null => {
    if (!meta) return null;
    return cloneSnapshot({ meta, nodes, edges, selectedId });
  }, [edges, meta, nodes, selectedId]);

  const pushHistory = useCallback((snapshot?: BuilderSnapshot | null) => {
    const next = snapshot ?? captureSnapshot();
    if (!next) return;
    setPast(current => [...current.slice(-49), cloneSnapshot(next)]);
    setFuture([]);
  }, [captureSnapshot]);

  const markChanged = useCallback(() => {
    setDirty(true);
    setSaveState('idle');
    setPreflight(null);
    setError(null);
  }, []);

  // `name` is the file slug this hydration should adopt (null for a brand
  // new, not-yet-named workflow) — kept separate from the YAML's own
  // `name:` field, which is a free-text display title and may not be a
  // valid file slug (e.g. an LLM-generated workflow title).
  const hydrateWorkflow = useCallback((
    workflow: YamlWorkflow,
    name: string | null,
    canvas?: WorkflowDraft['canvas'],
    options: { dirty?: boolean; selectedId?: string | null } = {},
  ) => {
    const flow = yamlToReactFlow(workflow);
    const restored = applyCanvasPositions(flow.nodes, canvas);
    const laidOut = restored ?? layoutFlow(flow.nodes, flow.edges, 'LR').nodes;
    const { nodes: ignoredNodes, edges: ignoredEdges, ...workflowMeta } = workflow;
    void ignoredNodes;
    void ignoredEdges;
    setNodes(laidOut);
    setEdges(flow.edges);
    setMeta(workflowMeta);
    setWorkflowName(name);
    setSelectedId(options.selectedId ?? canvas?.selected_node_id ?? null);
    setPast([]);
    setFuture([]);
    setDirty(Boolean(options.dirty));
    setPreflight(null);
    setSaveState('idle');
    setAutosaveState('idle');
    setError(null);
    if (canvas?.viewport) pendingViewport.current = canvas.viewport;
  }, [setEdges, setNodes]);

  useEffect(() => {
    if (!routeName) return;
    if (skipNextRouteLoad.current === routeName) {
      skipNextRouteLoad.current = null;
      return;
    }
    api.getWorkflow(routeName)
      .then(async ({ yaml }) => {
        setError(null);
        const workflow = parseYaml(yaml);
        let draft: WorkflowDraft | null = null;
        try {
          draft = await api.getWorkflowDraft(routeName);
        } catch {
          // A missing autosave is the normal case.
        }
        hydrateWorkflow(workflow, routeName, undefined, {
          selectedId: navState.builderResume?.selectedNodeId,
        });
        if (draft?.differs_from_current) setRecovery(draft);
      })
      .catch(async reason => {
        // Unsaved workflows launched from Builder still have a durable draft.
        try {
          const draft = await api.getWorkflowDraft(routeName);
          setError(null);
          hydrateWorkflow(parseYaml(draft.yaml), routeName, draft.canvas, {
            dirty: true,
            selectedId: navState.builderResume?.selectedNodeId,
          });
          setAutosaveState('recovered');
        } catch {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });
  }, [hydrateWorkflow, navState.builderResume?.selectedNodeId, routeName]);

  // Hydrate a workflow generated via "Generate from prompt" (Library) —
  // same shape as opening a saved workflow, just sourced from nav state
  // instead of a GET, and only for a brand-new (unnamed) Builder mount.
  // There's no file slug yet — Save routes through Save As.
  useEffect(() => {
    if (routeName || !navState.generatedYaml) return;
    // One-time hydration of a brand-new Builder mount from nav state, not a
    // sync-to-external-system loop — same justification as the analogous
    // effect in Cockpit.tsx.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    hydrateWorkflow(parseYaml(navState.generatedYaml), null, undefined, { dirty: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!rfInstance || !pendingViewport.current) return;
    const viewport = pendingViewport.current;
    pendingViewport.current = null;
    requestAnimationFrame(() => rfInstance.setViewport(viewport, { duration: 0 }));
  }, [rfInstance, nodes.length]);

  useEffect(() => {
    if (!dirty || !workflowName || !currentWorkflow || !NAME_PATTERN.test(workflowName)) return;
    const timer = window.setTimeout(() => {
      setAutosaveState('saving');
      api.saveWorkflowDraft(
        workflowName,
        dumpYaml(currentWorkflow),
        canvasFor(nodes, selectedId, rfInstance),
      )
        .then(() => setAutosaveState('saved'))
        .catch(reason => {
          setAutosaveState('error');
          setError(reason instanceof Error ? reason.message : String(reason));
        });
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [currentWorkflow, dirty, nodes, rfInstance, selectedId, workflowName]);

  const addNode = useCallback((typeName: string, position?: { x: number; y: number }) => {
    const manifest = findManifest(manifests, typeName);
    if (!manifest) return;
    const snapshot = captureSnapshot();
    if (snapshot) pushHistory(snapshot);
    let effectivePosition = position;
    if (!effectivePosition && rfInstance && canvasRef.current) {
      const bounds = canvasRef.current.getBoundingClientRect();
      effectivePosition = rfInstance.screenToFlowPosition({
        x: bounds.left + bounds.width / 2,
        y: bounds.top + bounds.height / 2,
      });
    }
    const existingIds = new Set(nodes.map(node => node.data.nodeId));
    const id = newNodeId(typeName, existingIds);
    const supportsModelSelection = Boolean(
      (manifest.config_schema as { properties?: Record<string, unknown> }).properties?.model,
    );
    const node: Node<WorkflowNodeData> = {
      id,
      type: 'workflow',
      position: effectivePosition ?? { x: 80 + nodes.length * 32, y: 100 + nodes.length * 24 },
      data: {
        nodeId: id,
        typeName,
        config: generateDefaults(manifest.config_schema) ?? {},
        ...(supportsModelSelection ? {
          selectedModel: 'auto',
          modelRouting: { accuracy_priority: 'maximum', prefer_low_latency: false },
        } : {}),
      },
    };
    setNodes(current => [...current, node]);
    setSelectedId(id);
    setShowInputs(false);
    setInspectorTab('configure');
    setInspectorOpen(true);
    markChanged();
  }, [captureSnapshot, manifests, markChanged, nodes, pushHistory, rfInstance, setNodes]);

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const typeName = event.dataTransfer.getData('application/reactflow');
    if (!typeName || !rfInstance) return;
    addNode(typeName, rfInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
  }, [addNode, rfInstance]);

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target) return;
    pushHistory();
    const groupId = `builder-edge-${crypto.randomUUID()}`;
    setEdges(current => addEdge({
      ...connection,
      id: groupId,
      type: 'smoothstep',
      data: { edgeKind: 'simple', groupId },
    }, current));
    markChanged();
  }, [markChanged, pushHistory, setEdges]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const positionChange = changes.find(change => change.type === 'position');
    if (positionChange?.type === 'position' && positionChange.dragging && !dragStart.current) {
      dragStart.current = captureSnapshot();
    }
    if (positionChange?.type === 'position' && positionChange.dragging === false && dragStart.current) {
      pushHistory(dragStart.current);
      dragStart.current = null;
      markChanged();
    }
    if (changes.some(change => change.type === 'remove')) {
      pushHistory();
      markChanged();
      const removed = new Set(
        changes.filter(change => change.type === 'remove').map(change => change.id),
      );
      if (selectedId && removed.has(selectedId)) setSelectedId(null);
    }
    applyNodeChanges(changes);
  }, [applyNodeChanges, captureSnapshot, markChanged, pushHistory, selectedId]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    if (changes.some(change => change.type === 'remove')) {
      pushHistory();
      markChanged();
    }
    applyEdgeChanges(changes);
  }, [applyEdgeChanges, markChanged, pushHistory]);

  const onConfigChange = useCallback((nextConfig: Record<string, unknown>) => {
    if (!selectedId) return;
    pushHistory();
    setNodes(current => current.map(node => node.id === selectedId
      ? { ...node, data: { ...node.data, config: nextConfig } }
      : node));
    markChanged();
  }, [markChanged, pushHistory, selectedId, setNodes]);

  const onExperienceChange = useCallback((experience: NodeExperienceSpec | undefined) => {
    if (!selectedId) return;
    pushHistory();
    setNodes(current => current.map(node => node.id === selectedId
      ? { ...node, data: { ...node.data, experience } }
      : node));
    markChanged();
  }, [markChanged, pushHistory, selectedId, setNodes]);

  const onIdChange = useCallback((nextId: string) => {
    if (!selectedId || nextId === selectedId) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(nextId)) {
      setError('Node IDs must start with a letter or underscore and contain only letters, numbers and underscores.');
      return;
    }
    if (nodes.some(node => node.id === nextId && node.id !== selectedId)) {
      setError(`Node ID "${nextId}" is already used.`);
      return;
    }
    pushHistory();
    setNodes(current => current.map(node => {
      // Every node's config can hold `{{selectedId.field}}`/`{{outputs.selectedId}}`
      // template tokens addressing this node by its old id — those are
      // references just like an edge, and must move with the rename.
      const config = renameNodeReferencesInConfig(node.data.config, selectedId, nextId);
      return node.id === selectedId
        ? { ...node, id: nextId, data: { ...node.data, nodeId: nextId, config } }
        : (config !== node.data.config ? { ...node, data: { ...node.data, config } } : node);
    }));
    setEdges(current => current.map(edge => ({
      ...edge,
      source: edge.source === selectedId ? nextId : edge.source,
      target: edge.target === selectedId ? nextId : edge.target,
    })));
    setMeta(current => {
      if (!current) return current;
      const replace = (value: string) => value === selectedId ? nextId : value;
      const output = current.output as {
        include_input?: boolean;
        nodes?: Array<{ node_id: string; flatten?: boolean }>;
      } | undefined;
      return {
        ...current,
        entry: current.entry ? replace(current.entry) : current.entry,
        exit: Array.isArray(current.exit)
          ? current.exit.map(replace)
          : current.exit
            ? replace(current.exit)
            : current.exit,
        output: output ? {
          ...output,
          nodes: (output.nodes ?? []).map(item => ({
            ...item,
            node_id: replace(item.node_id),
          })),
        } : current.output,
      };
    });
    setSelectedId(nextId);
    markChanged();
  }, [markChanged, nodes, pushHistory, selectedId, setEdges, setNodes]);

  const onModelSelectionChange = useCallback((nextModel: string | null) => {
    if (!selectedId) return;
    pushHistory();
    setNodes(current => current.map(node => node.id === selectedId
      ? { ...node, data: { ...node.data, selectedModel: nextModel } }
      : node));
    markChanged();
  }, [markChanged, pushHistory, selectedId, setNodes]);

  const onModelRoutingChange = useCallback((nextPolicy: ModelRoutingPolicy | undefined) => {
    if (!selectedId) return;
    pushHistory();
    setNodes(current => current.map(node => node.id === selectedId
      ? { ...node, data: { ...node.data, modelRouting: nextPolicy } }
      : node));
    markChanged();
  }, [markChanged, pushHistory, selectedId, setNodes]);

  const onInputsChange = useCallback((inputs: Record<string, WorkflowInputSpec>) => {
    pushHistory();
    setMeta(current => current ? { ...current, inputs } : current);
    markChanged();
  }, [markChanged, pushHistory]);

  const undo = useCallback(() => {
    const previous = past[past.length - 1];
    const current = captureSnapshot();
    if (!previous || !current) return;
    setFuture(items => [cloneSnapshot(current), ...items].slice(0, 50));
    setPast(items => items.slice(0, -1));
    setMeta(previous.meta);
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setSelectedId(previous.selectedId);
    markChanged();
  }, [captureSnapshot, markChanged, past, setEdges, setNodes]);

  const redo = useCallback(() => {
    const next = future[0];
    const current = captureSnapshot();
    if (!next || !current) return;
    setPast(items => [...items.slice(-49), cloneSnapshot(current)]);
    setFuture(items => items.slice(1));
    setMeta(next.meta);
    setNodes(next.nodes);
    setEdges(next.edges);
    setSelectedId(next.selectedId);
    markChanged();
  }, [captureSnapshot, future, markChanged, setEdges, setNodes]);

  const validate = useCallback(async (workflow = currentWorkflow) => {
    if (!workflow) return null;
    setValidating(true);
    setError(null);
    try {
      const report = await api.validateWorkflow(dumpYaml(workflow));
      setPreflight(report);
      return report;
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setValidating(false);
    }
  }, [currentWorkflow]);

  const autofix = useCallback(async () => {
    if (!currentWorkflow) return;
    setAutofixing(true);
    setError(null);
    try {
      const result = await api.autofixWorkflow(dumpYaml(currentWorkflow));
      const fixedWorkflow = parseYaml(result.yaml);
      hydrateWorkflow(fixedWorkflow, workflowName, undefined, { dirty: true });
      setInspectorOpen(true);
      setShowInputs(false);
      setInspectorTab('checks');
      await validate(fixedWorkflow);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAutofixing(false);
    }
  }, [currentWorkflow, hydrateWorkflow, validate, workflowName]);

  const onSave = useCallback(async () => {
    if (!currentWorkflow) return;
    // A brand-new, generated-and-not-yet-named workflow has no file slug
    // yet — collect one (with a collision guard, since POST
    // /workflows/save overwrites unconditionally) before it can be saved.
    if (!workflowName) {
      setShowSaveAs(true);
      return;
    }
    const report = await validate(currentWorkflow);
    if (!report?.valid) {
      setInspectorOpen(true);
      setShowInputs(false);
      setInspectorTab('checks');
      return;
    }
    setSaveState('saving');
    try {
      await api.saveWorkflow(workflowName, dumpYaml(currentWorkflow));
      setDirty(false);
      setSaveState('saved');
      setAutosaveState('idle');
      setRecovery(null);
      if (routeName !== workflowName) {
        navigate(`/builder/${workflowName}`, { replace: true });
      }
      window.setTimeout(() => setSaveState('idle'), 1800);
    } catch (reason: unknown) {
      setSaveState('error');
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [currentWorkflow, navigate, routeName, validate, workflowName]);

  const onSaveAsConfirm = useCallback(
    async ({ displayName, slug }: { displayName: string; slug: string }) => {
      if (!meta) return;
      const workflow = reactFlowToYaml({ ...meta, name: displayName }, nodes, edges);
      await api.saveWorkflow(slug, dumpYaml(workflow));
      // No draft could exist under `slug` from this session, but a prior
      // abandoned session may have left one — clear it defensively so a
      // later load doesn't offer to "resume" stale content.
      await api.deleteWorkflowDraft(slug).catch(() => undefined);
      setMeta(current => (current ? { ...current, name: displayName } : current));
      setWorkflowName(slug);
      setShowSaveAs(false);
      setDirty(false);
      setSaveState('saved');
      setAutosaveState('idle');
      window.setTimeout(() => setSaveState('idle'), 1500);
      // Now that the file exists, route to it so subsequent saves go
      // straight through onSave's `workflowName` branch instead of
      // re-prompting.
      navigate(`/builder/${slug}`, { replace: true });
    },
    [edges, meta, navigate, nodes],
  );

  const prepareRun = useCallback(async (workflow: YamlWorkflow, title: string) => {
    const report = await validate(workflow);
    if (!report?.valid) {
      setInspectorOpen(true);
      setShowInputs(false);
      setInspectorTab('checks');
      return;
    }
    if (workflowName) {
      try {
        // Guarantees that Back to Builder also works for a not-yet-manually-saved workflow.
        await api.saveWorkflowDraft(
          workflowName,
          currentYaml,
          canvasFor(nodes, selectedId, rfInstance),
        );
      } catch {
        // Running remains available if the recovery copy could not be refreshed;
        // the error is already surfaced by the normal autosave status.
      }
    }
    setRunDraft({ workflow, title });
  }, [currentYaml, nodes, rfInstance, selectedId, validate, workflowName]);

  const autoLayout = useCallback(() => {
    pushHistory();
    setNodes(current => layoutFlow(current, edges, 'LR').nodes);
    markChanged();
    requestAnimationFrame(() => rfInstance?.fitView({ padding: 0.2, duration: 350 }));
  }, [edges, markChanged, pushHistory, rfInstance, setNodes]);

  const restoreAutosave = useCallback(() => {
    if (!recovery) return;
    hydrateWorkflow(parseYaml(recovery.yaml), workflowName, recovery.canvas, { dirty: true });
    setRecovery(null);
    setAutosaveState('recovered');
  }, [hydrateWorkflow, recovery, workflowName]);

  const discardAutosave = useCallback(() => {
    if (!workflowName) return;
    api.deleteWorkflowDraft(workflowName).catch(() => undefined);
    setRecovery(null);
    setAutosaveState('idle');
  }, [workflowName]);

  const startBlank = useCallback((newName: string) => {
    const workflow: YamlWorkflow = {
      name: newName,
      description: '',
      version: '1.0',
      use_case: 'generic',
      inputs: {},
      static_variables: [],
      nodes: [],
      edges: [],
    };
    hydrateWorkflow(workflow, newName, undefined, { dirty: true });
    // Adopt the route immediately so a reload before the first manual Save
    // can still recover from the autosaved draft instead of losing the
    // in-memory canvas entirely.
    skipNextRouteLoad.current = newName;
    navigate(`/builder/${newName}`, { replace: true });
    setPaletteOpen(true);
    setInspectorOpen(true);
  }, [hydrateWorkflow, navigate]);

  const startTemplate = useCallback(async (newName: string, templateName: string) => {
    setLoadingTemplate(true);
    setError(null);
    try {
      const result = await api.getWorkflow(templateName);
      const workflow = parseYaml(result.yaml);
      hydrateWorkflow({ ...workflow, name: newName, version: '1.0' }, newName, undefined, { dirty: true });
      skipNextRouteLoad.current = newName;
      navigate(`/builder/${newName}`, { replace: true });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingTemplate(false);
    }
  }, [hydrateWorkflow, navigate]);

  const path = useMemo(() => relatedPath(selectedId, edges), [edges, selectedId]);
  const issueNodes = useMemo(() => new Set(
    (preflight?.issues ?? []).map(issue => issue.node_id).filter(Boolean) as string[],
  ), [preflight]);
  const displayNodes = useMemo(() => nodes.map(node => ({
    ...node,
    data: {
      ...node.data,
      downstreamCount: edges.filter(edge => edge.source === node.id).length,
      hasIssue: issueNodes.has(node.id),
      faded: selectedId != null && !path.has(node.id),
    },
  })), [edges, issueNodes, nodes, path, selectedId]);
  const displayEdges = useMemo(() => edges.map(edge => {
    const highlighted = selectedId != null && path.has(edge.source) && path.has(edge.target);
    return {
      ...edge,
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
      style: {
        stroke: highlighted ? 'var(--graph-edge-active)' : 'var(--graph-edge)',
        strokeWidth: highlighted ? 2.2 : 1.35,
        opacity: selectedId != null && !highlighted ? 0.28 : 1,
      },
      labelStyle: { fill: 'var(--text-secondary)', fontSize: 10, fontWeight: 600 },
      labelBgStyle: { fill: 'var(--surface-primary)', fillOpacity: 0.94 },
    };
  }), [edges, path, selectedId]);

  if (!routeName && !meta && !navState.generatedYaml) {
    return (
      <div className="h-full">
        {loadingTemplate && <div className="absolute inset-0 z-40 grid place-items-center bg-white/70"><Spinner label="Preparing template…" /></div>}
        <BuilderStart onBlank={startBlank} onTemplate={startTemplate} />
        {error && <div className="fixed bottom-5 right-5 z-50 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-panel">{error}</div>}
      </div>
    );
  }

  if (!meta || !currentWorkflow) {
    return (
      <div className="grid h-full place-items-center">
        {error ? (
          <div className="max-w-lg rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
            <div className="font-semibold">Could not open this workflow</div>
            <div className="mt-1">{error}</div>
            <button className="ui-button ui-button--secondary mt-4" onClick={() => navigate('/library')} type="button">Back to Library</button>
          </div>
        ) : <Spinner label="Preparing Builder…" />}
      </div>
    );
  }

  const autosaveLabel = !workflowName
    ? 'Not saved yet'
    : !dirty
      ? 'Saved'
      : autosaveState === 'saving'
        ? 'Autosaving…'
        : autosaveState === 'error'
          ? 'Autosave failed'
          : autosaveState === 'recovered'
            ? 'Recovered draft'
            : autosaveState === 'saved'
              ? 'Draft autosaved'
              : 'Unsaved changes';
  const returnPath = workflowName ? `/builder/${encodeURIComponent(workflowName)}` : undefined;

  return (
    <div className="builder-shell flex h-full min-h-0 flex-col">
      <header className="builder-actionbar">
        <div className="builder-actionbar-primary">
          <button
            aria-pressed={paletteOpen}
            className={paletteOpen ? 'ui-button builder-action-active' : 'ui-button ui-button--secondary'}
            onClick={() => setPaletteOpen(value => !value)}
            type="button"
          >
            <Icon name="layout" size={15} /> Nodes
          </button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-semibold text-ink-950" title={meta.name}>{meta.name}</h2>
              <span className={`builder-save-indicator ${dirty ? 'builder-save-indicator--dirty' : 'builder-save-indicator--saved'}`}>
                {autosaveLabel}
              </span>
            </div>
            <div className="mt-0.5 hidden text-[10px] text-ink-500 sm:block">
              {nodes.length} nodes · {edges.length} connections · workflow v{meta.version ?? '1.0'}
              {workflowName ? ` · ${workflowName}.yaml` : ''}
            </div>
          </div>
        </div>

        <div className="builder-actionbar-actions" role="toolbar" aria-label="Workflow Builder actions">
          <button aria-label="Undo" className="ui-icon-button" disabled={past.length === 0} onClick={undo} title="Undo" type="button"><Icon name="undo" size={15} /></button>
          <button aria-label="Redo" className="ui-icon-button" disabled={future.length === 0} onClick={redo} title="Redo" type="button"><Icon name="redo" size={15} /></button>
          <span className="builder-toolbar-separator" />
          <button className="ui-button ui-button--secondary" onClick={() => { setShowInputs(true); setInspectorOpen(true); }} type="button">
            Inputs <span className="builder-action-count">{Object.keys(meta.inputs ?? {}).length}</span>
          </button>
          <button className="ui-button ui-button--secondary" onClick={autoLayout} title="Arrange left to right. Manual positions remain stable until you use this action." type="button">Auto-layout</button>
          {workflowName && (
            <button className="ui-button ui-button--secondary" onClick={() => setVersionHistoryOpen(true)} type="button"><Icon name="history" size={14} /> Versions</button>
          )}
          <button className="ui-button ui-button--secondary" disabled={validating} onClick={() => { setShowInputs(false); setInspectorOpen(true); setInspectorTab('checks'); void validate(); }} type="button">
            <Icon name="check" size={14} /> {validating ? 'Checking…' : 'Preflight'}
          </button>
          {preflight && !preflight.valid && (
            <button className="ui-button ui-button--secondary" disabled={autofixing} onClick={() => void autofix()} type="button">
              <Icon name="check" size={14} /> {autofixing ? 'Fixing…' : 'Auto-fix'}
            </button>
          )}
          <button className="ui-button ui-button--secondary" disabled={nodes.length === 0} onClick={() => void prepareRun(currentWorkflow, 'Full workflow')} type="button"><Icon name="play" size={14} /> Run in Cockpit</button>
          <button className="ui-button ui-button--primary" disabled={saveState === 'saving'} onClick={() => void onSave()} type="button"><Icon name="save" size={14} /> {saveState === 'saving' ? 'Saving…' : 'Save'}</button>
          <button
            aria-pressed={inspectorOpen}
            className={inspectorOpen ? 'ui-button builder-action-active' : 'ui-button ui-button--secondary'}
            onClick={() => setInspectorOpen(value => !value)}
            type="button"
          >
            Inspector
          </button>
        </div>
      </header>

      {recovery && (
        <div className="builder-recovery-banner" role="status">
          <div className="min-w-0 flex-1">
            <span className="font-semibold text-ink-900">A newer autosave is available.</span>
            <span className="ml-2 text-ink-600">Saved {new Date(recovery.updated_at).toLocaleString()}.</span>
          </div>
          <button className="ui-button ui-button--primary" onClick={restoreAutosave} type="button">Resume draft</button>
          <button className="ui-button ui-button--secondary" onClick={discardAutosave} type="button">Discard</button>
        </div>
      )}

      {error && (
        <div className="builder-error-banner" role="alert">
          <span className="min-w-0 flex-1 truncate">{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError(null)} type="button">×</button>
        </div>
      )}

      <div className="builder-workspace flex min-h-0 flex-1">
        {paletteOpen && (
          <aside className="builder-palette" aria-label="Node library">
            <NodePalette types={manifests} onAdd={typeName => addNode(typeName)} onClose={() => setPaletteOpen(false)} />
          </aside>
        )}

        <main
          className="builder-canvas relative min-w-0 flex-1"
          onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
          onDrop={onDrop}
          ref={canvasRef}
        >
          <ReactFlow
            deleteKeyCode={['Backspace', 'Delete']}
            edges={displayEdges}
            fitView
            maxZoom={1.8}
            minZoom={0.18}
            nodeTypes={nodeTypes}
            nodes={displayNodes}
            onConnect={onConnect}
            onEdgesChange={onEdgesChange}
            onInit={instance => {
              setRfInstance(instance);
              if (pendingViewport.current) {
                const viewport = pendingViewport.current;
                pendingViewport.current = null;
                requestAnimationFrame(() => instance.setViewport(viewport, { duration: 0 }));
              }
            }}
            onNodeClick={(_, node) => {
              setSelectedId(node.id);
              setShowInputs(false);
              setInspectorOpen(true);
              setInspectorTab('configure');
              if (window.innerWidth <= 900) setPaletteOpen(false);
            }}
            onNodesChange={onNodesChange}
            onPaneClick={() => setSelectedId(null)}
          >
            <Background color="var(--border-default)" gap={22} size={1} />
            <Controls position="bottom-right" />
            <MiniMap className="builder-minimap" maskColor="rgba(242, 251, 250, 0.76)" nodeColor="var(--brand-teal-600)" pannable zoomable />
          </ReactFlow>

          <div className="builder-canvas-status">
            <span>{Math.round((rfInstance?.getZoom() ?? 1) * 100)}%</span>
            <button onClick={() => rfInstance?.fitView({ padding: 0.2, duration: 300 })} type="button">Fit workflow</button>
            {selectedId && <button onClick={() => setSelectedId(null)} type="button">Clear focus</button>}
          </div>

          {nodes.length === 0 && (
            <div className="builder-empty-canvas">
              <div className="builder-empty-icon"><Icon name="topology" size={22} /></div>
              <div className="mt-3 text-base font-semibold text-ink-900">Your workflow canvas is ready</div>
              <div className="mt-1 max-w-sm text-center text-xs leading-5 text-ink-500">
                Add a node from the library. Connect outputs on the right to inputs on the left, then map data in the Inspector.
              </div>
              <button className="ui-button ui-button--primary mt-4" onClick={() => setPaletteOpen(true)} type="button">Browse node library</button>
            </div>
          )}
        </main>

        {inspectorOpen && (
          <aside className="builder-inspector" aria-label="Builder inspector">
            <BuilderInspector
              edges={edges}
              llmModels={llmModels}
              manifests={manifests}
              nodes={nodes}
              onAutofix={() => void autofix()}
              autofixing={autofixing}
              onClose={() => setInspectorOpen(false)}
              onCloseInputs={() => setShowInputs(false)}
              onConfigChange={onConfigChange}
              onExperienceChange={onExperienceChange}
              onIdChange={onIdChange}
              onInputsChange={onInputsChange}
              onLaunchTest={(workflow, title) => void prepareRun(workflow, title)}
              onModelRoutingChange={onModelRoutingChange}
              onModelSelectionChange={onModelSelectionChange}
              onRunWorkflow={() => void prepareRun(currentWorkflow, 'Full workflow')}
              onSelectNode={nodeId => {
                setSelectedId(nodeId);
                setShowInputs(false);
                setInspectorTab('configure');
              }}
              onTabChange={tab => { setShowInputs(false); setInspectorTab(tab); }}
              onTestWorkflow={() => {
                setShowInputs(false);
                setInspectorTab('checks');
                void validate();
              }}
              onValidate={() => void validate()}
              preflight={preflight}
              selected={selected}
              showInputs={showInputs}
              tab={inspectorTab}
              validating={validating}
              workflow={currentWorkflow}
            />
          </aside>
        )}
      </div>

      {versionHistoryOpen && workflowName && (
        <VersionHistoryPanel
          currentYaml={currentYaml}
          onClose={() => setVersionHistoryOpen(false)}
          onRestored={yaml => {
            hydrateWorkflow(parseYaml(yaml), workflowName);
            setVersionHistoryOpen(false);
            setSaveState('saved');
          }}
          workflowName={workflowName}
        />
      )}

      {showSaveAs && (
        <SaveAsDialog
          initialDisplayName={meta.name}
          onCancel={() => setShowSaveAs(false)}
          onConfirm={onSaveAsConfirm}
        />
      )}

      {runDraft && (
        <RunDialog
          inputs={runDraft.workflow.inputs ?? {}}
          launchContext={{
            builderReturnPath: returnPath,
            selectedNodeId: selectedId,
            viewport: rfInstance?.getViewport(),
            testLabel: runDraft.title,
          }}
          onClose={() => setRunDraft(null)}
          workflowName={runDraft.title}
          workflowYaml={dumpYaml(runDraft.workflow)}
        />
      )}
    </div>
  );
}
