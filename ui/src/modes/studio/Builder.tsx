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
  Position,
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
  IntegrationConnectionInfo,
  LLMModelInfo,
  NodeTypeManifest,
  WorkflowDraft,
  WorkflowPreflightReport,
} from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { Icon } from '../../components/ui/Icon';
import { BuilderInspector, type BuilderInspectorTab } from './BuilderInspector';
import { BuilderStart } from './BuilderStart';
import { pruneNodeReferencesInConfig, renameNodeReferencesInConfig } from './builder-graph';
import { generateDefaults, findManifest, newNodeId } from './builder-helpers';
import { InfoPopover } from './builder/InfoPopover';
import { isNoteNodeId, NOTE_ID_PREFIX, NOTE_NODE_TYPE, NoteNode } from './builder/NoteNode';
import { NodeSearchPalette } from './builder/NodeSearchPalette';
import { BuilderStageBandNode, BuilderStagePlaceholderNode } from './builder/StageNodes';
import {
  applyStageCollapse,
  buildStageBandNodes,
  BUILDER_STAGE_BAND_TYPE,
  BUILDER_STAGE_PLACEHOLDER_TYPE,
  collapsibleStageIndexes,
  isSyntheticNodeId,
} from './builder/stage-view';
import { groupIntoStages, layoutFlow } from './flow-layout';
import {
  buildWorkflowSvg,
  downloadBlob,
  exportFileName,
  svgToPngBlob,
} from './graph-export';
import {
  ARROW_DIRECTIONS,
  DEFAULT_NODE_HEIGHT,
  DEFAULT_NODE_WIDTH,
  resolveArrowTarget,
} from './graph-navigation';
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
  type NoteSpec,
  type WorkflowEdgeData,
  type WorkflowInputSpec,
  type WorkflowNodeData,
  type YamlWorkflow,
} from './yaml-bridge';

const nodeTypes = {
  workflow: WorkflowNode,
  [BUILDER_STAGE_BAND_TYPE]: BuilderStageBandNode,
  [BUILDER_STAGE_PLACEHOLDER_TYPE]: BuilderStagePlaceholderNode,
  [NOTE_NODE_TYPE]: NoteNode,
};
const NOTE_DEFAULT_SIZE = { width: 220, height: 140 };
const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;
// Semantic-zoom tiers, with a gap between them so a node's appearance doesn't
// flicker while the user is scrubbing the zoom around the threshold.
const COMPACT_ENTER_ZOOM = 0.5;
const COMPACT_EXIT_ZOOM = 0.62;
// Reading a step means reading its detail, so jumping to one from search or the
// minimap zooms in far enough for the detail tier to be showing.
const FOCUS_MIN_ZOOM = 0.7;

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
  const [inspectorWide, setInspectorWide] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<BuilderInspectorTab>('configure');
  // Steps the most recent simulation actually executed, and the gate it is
  // parked at. Lighting the real path on the canvas is what turns a simulation
  // from a JSON dump into a demonstration of the process.
  const [simulationPath, setSimulationPath] = useState<Set<string>>(new Set());
  const [simulationWaiting, setSimulationWaiting] = useState<Set<string>>(new Set());
  // Operation class per "<server>:<tool>", discovered from the MCP servers the
  // workflow actually uses. Lets the canvas show READ or WRITE on a step
  // without opening it — the classification lives on the server and in the
  // deployment's policy, so it cannot be derived in the browser.
  const [mcpOperations, setMcpOperations] = useState<Map<string, string>>(new Map());
  // Whether any IntegrationAgent/EmailAgent connection on the canvas needs
  // reauthorization — live account state, fetched independently of the
  // node-type manifest (same reasoning as mcpOperations above: canvas badges
  // and the config panel's own connection list are two different consumers
  // of the same backend data, each fetching what it needs).
  const [integrationConnections, setIntegrationConnections] = useState<IntegrationConnectionInfo[]>([]);
  // Last real output per node, from either single-step testing or a full
  // simulation run — lifted here (not into BuilderInspector) because the
  // inspector panel unmounts on close, which would otherwise throw a step's
  // captured test value away the moment its panel is collapsed. Feeds the
  // Inputs tab's "Ran: ..." value previews.
  const [nodeRunOutputs, setNodeRunOutputs] = useState<Record<string, Record<string, unknown>>>({});
  const recordNodeOutput = useCallback((nodeId: string, output: Record<string, unknown> | null | undefined) => {
    if (!output) return;
    setNodeRunOutputs(prev => ({ ...prev, [nodeId]: output }));
  }, []);
  const [showInputs, setShowInputs] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [autosaveState, setAutosaveState] = useState<'idle' | 'saving' | 'saved' | 'error' | 'recovered'>('idle');
  const [autosaveError, setAutosaveError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Serializes autosave PUTs so a slow request can never arrive at the server
  // after a later one and overwrite newer content with stale content; `seq`
  // guards UI state updates against the same reordering.
  const autosaveChainRef = useRef<Promise<void>>(Promise.resolve());
  const autosaveSeqRef = useRef(0);
  const [validating, setValidating] = useState(false);
  const [preflight, setPreflight] = useState<WorkflowPreflightReport | null>(null);
  const [autofixing, setAutofixing] = useState(false);
  const [recovery, setRecovery] = useState<WorkflowDraft | null>(null);
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [runDraft, setRunDraft] = useState<RunDraft | null>(null);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [past, setPast] = useState<BuilderSnapshot[]>([]);
  const [future, setFuture] = useState<BuilderSnapshot[]>([]);
  // Reading a long workflow: the canvas can take over the whole window, group
  // itself into stage columns (collapsing the parallel ones), flow top-down
  // instead of left-to-right, and drop to a lower level of detail when zoomed
  // out. None of this touches the workflow — it is all view state.
  const [expanded, setExpanded] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [detailTier, setDetailTier] = useState<'detail' | 'compact'>('detail');
  const [layoutDirection, setLayoutDirection] = useState<'LR' | 'TB'>('LR');
  const [showStages, setShowStages] = useState(false);
  const [collapsedStages, setCollapsedStages] = useState<Set<number>>(new Set());
  const [searchOpen, setSearchOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState<'png' | 'svg' | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  // Which side panels to put back when leaving the expanded view — expanding
  // closes them for the canvas width, and silently reopening both afterwards
  // would be wrong for anyone who had them closed to begin with.
  const panelsBeforeExpand = useRef<{ palette: boolean; inspector: boolean } | null>(null);
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

  const currentWorkflow = useMemo(() => {
    if (!meta) return null;
    // Notes ride along in the same react-flow `nodes` array (so drag/select/
    // delete/undo all work for free) but must never reach reactFlowToYaml —
    // they are not workflow steps and carry none of NodeSpec's required
    // shape. The canvas is their single source of truth: whatever note
    // nodes currently exist there is exactly what gets written to `notes:`.
    const workflowNodes = nodes.filter(node => !isNoteNodeId(node.id));
    const noteSpecs: NoteSpec[] = nodes
      .filter(node => isNoteNodeId(node.id))
      .map(node => ({ id: node.id, text: node.data.noteText ?? '', position: node.position }));
    const built = reactFlowToYaml(meta, workflowNodes, edges);
    return noteSpecs.length > 0 ? { ...built, notes: noteSpecs } : built;
  }, [edges, meta, nodes]);
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
    const laidOut = restored ?? layoutFlow(flow.nodes, flow.edges, layoutDirection).nodes;
    // Notes are a Builder-only annotation layer (see builder/NoteNode.tsx) —
    // never part of yamlToReactFlow's output, so they're rebuilt here
    // straight from the workflow's own `notes:` key, keeping their saved
    // position exactly rather than being auto-laid-out with the real steps.
    const noteNodes: Node<WorkflowNodeData>[] = (workflow.notes ?? []).map(note => ({
      id: note.id,
      type: NOTE_NODE_TYPE,
      position: note.position,
      style: NOTE_DEFAULT_SIZE,
      data: { nodeId: note.id, typeName: '__note__', config: {}, noteText: note.text },
    }));
    const { nodes: ignoredNodes, edges: ignoredEdges, notes: ignoredNotes, ...workflowMeta } = workflow;
    void ignoredNodes;
    void ignoredEdges;
    void ignoredNotes;
    setNodes([...laidOut, ...noteNodes]);
    setEdges(flow.edges);
    setMeta(workflowMeta);
    setWorkflowName(name);
    setSelectedId(options.selectedId ?? canvas?.selected_node_id ?? null);
    setPast([]);
    setFuture([]);
    // Stage indexes are positional, so they mean nothing once a different graph
    // is on the canvas.
    setCollapsedStages(new Set());
    setDirty(Boolean(options.dirty));
    setPreflight(null);
    setSaveState('idle');
    setAutosaveState('idle');
    setError(null);
    if (canvas?.viewport) pendingViewport.current = canvas.viewport;
  }, [layoutDirection, setEdges, setNodes]);

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
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      hydrateWorkflow(parseYaml(navState.generatedYaml), null, undefined, { dirty: true });
    } catch (e) {
      // A generation that failed its static check can still be "opened" —
      // the YAML it hands back may be structurally invalid (e.g. no nodes).
      setError(
        `Couldn't load the generated workflow: ${e instanceof Error ? e.message : String(e)}. ` +
        'The model likely returned an invalid workflow — try generating again.',
      );
    }
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
      const seq = ++autosaveSeqRef.current;
      setAutosaveState('saving');
      const yaml = dumpYaml(currentWorkflow);
      const canvas = canvasFor(nodes, selectedId, rfInstance);
      // Chained onto the previous attempt so PUTs reach the server in the
      // order they were made, instead of racing as independent concurrent
      // requests that could arrive out of order and clobber newer content.
      autosaveChainRef.current = autosaveChainRef.current
        .catch(() => {})
        .then(() => api.saveWorkflowDraft(workflowName, yaml, canvas))
        .then(() => {
          if (seq !== autosaveSeqRef.current) return;
          setAutosaveState('saved');
          setAutosaveError(null);
        })
        .catch(reason => {
          if (seq !== autosaveSeqRef.current) return;
          setAutosaveState('error');
          setAutosaveError(reason instanceof Error ? reason.message : String(reason));
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
    const defaultConfig = generateDefaults(manifest.config_schema) ?? {};
    if (typeName === 'RouterAgent') {
      // A fresh Router shouldn't inherit the backend schema's `mode: "rule"`
      // default — that's a legacy identity nobody chose, and it renders as a
      // dead end (deprecation banner, no editor for its rules). Leaving
      // `mode` unset forces the author through the mode picker instead.
      delete (defaultConfig as Record<string, unknown>).mode;
    }
    const node: Node<WorkflowNodeData> = {
      id,
      type: 'workflow',
      position: effectivePosition ?? { x: 80 + nodes.length * 32, y: 100 + nodes.length * 24 },
      data: {
        nodeId: id,
        typeName,
        config: defaultConfig,
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

  // A note is a personal annotation, not a workflow step — see
  // builder/NoteNode.tsx. It shares the canvas's `nodes` array so drag,
  // select, delete and undo all work through the same generic machinery as
  // every real step, but it is filtered out before the workflow is built
  // (see `currentWorkflow`) and never touches the manifest/config machinery.
  const addNote = useCallback(() => {
    const snapshot = captureSnapshot();
    if (snapshot) pushHistory(snapshot);
    let position = { x: 80, y: 80 };
    if (rfInstance && canvasRef.current) {
      const bounds = canvasRef.current.getBoundingClientRect();
      position = rfInstance.screenToFlowPosition({
        x: bounds.left + bounds.width / 2 - NOTE_DEFAULT_SIZE.width / 2,
        y: bounds.top + bounds.height / 2 - NOTE_DEFAULT_SIZE.height / 2,
      });
    }
    const id = `${NOTE_ID_PREFIX}${crypto.randomUUID()}`;
    const note: Node<WorkflowNodeData> = {
      id,
      type: NOTE_NODE_TYPE,
      position,
      style: NOTE_DEFAULT_SIZE,
      data: { nodeId: id, typeName: '__note__', config: {}, noteText: '' },
    };
    setNodes(current => [...current, note]);
    markChanged();
  }, [captureSnapshot, markChanged, pushHistory, rfInstance, setNodes]);

  const updateNoteText = useCallback((noteId: string, text: string) => {
    setNodes(current => current.map(node => (
      node.id === noteId ? { ...node, data: { ...node.data, noteText: text } } : node
    )));
    markChanged();
  }, [markChanged, setNodes]);

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

  const onNodesChange = useCallback((allChanges: NodeChange[]) => {
    // Stage bands and collapsed-stage placeholders are drawn on top of the
    // workflow, not part of it: their measurements and selection must never
    // reach the node state that becomes YAML.
    const changes = allChanges.filter(change => !('id' in change) || !isSyntheticNodeId(change.id));
    if (changes.length === 0) return;
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
      // A deleted node's id can still be addressed by every other node's
      // template tokens (`{{outputs.deletedId.field}}`, a DecisionAgent
      // `field: outputs.deletedId...` condition, a DataTransformAgent
      // `$deletedId.field` value) — those are references just like an edge,
      // and left behind they surface as TEMPLATE_UNKNOWN_NODE preflight
      // errors autofix can only resolve when the "did you mean" match is
      // unambiguous. Scrub them here the same way onIdChange scrubs a
      // rename, and drop the node from entry/exit/output.nodes too.
      setNodes(current => current.map(node => {
        if (removed.has(node.id)) return node;
        let config = node.data.config;
        for (const deletedId of removed) {
          config = pruneNodeReferencesInConfig(config, deletedId);
        }
        return config !== node.data.config
          ? { ...node, data: { ...node.data, config } }
          : node;
      }));
      setMeta(current => {
        if (!current) return current;
        const output = current.output as {
          include_input?: boolean;
          nodes?: Array<{ node_id: string; flatten?: boolean }>;
        } | undefined;
        return {
          ...current,
          entry: current.entry && removed.has(current.entry) ? undefined : current.entry,
          exit: Array.isArray(current.exit)
            ? current.exit.filter(id => !removed.has(id))
            : current.exit && removed.has(current.exit)
              ? undefined
              : current.exit,
          output: output ? {
            ...output,
            nodes: (output.nodes ?? []).filter(item => !removed.has(item.node_id)),
          } : current.output,
        };
      });
    }
    applyNodeChanges(changes);
  }, [applyNodeChanges, captureSnapshot, markChanged, pushHistory, selectedId, setMeta, setNodes]);

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

  const autoLayout = useCallback((direction = layoutDirection) => {
    pushHistory();
    // Notes have no place in the dagre grid — auto-layout only rearranges
    // real steps, notes keep whatever position the author dragged them to.
    setNodes(current => {
      const workflowNodes = current.filter(node => !isNoteNodeId(node.id));
      const noteNodes = current.filter(node => isNoteNodeId(node.id));
      return [...layoutFlow(workflowNodes, edges, direction).nodes, ...noteNodes];
    });
    markChanged();
    requestAnimationFrame(() => rfInstance?.fitView({ padding: 0.2, duration: 350 }));
  }, [edges, layoutDirection, markChanged, pushHistory, rfInstance, setNodes]);

  // Switching direction without re-running the layout would leave every node
  // where it was with its handles moved to the wrong edges, so the two are one
  // action.
  const toggleLayoutDirection = useCallback(() => {
    const next = layoutDirection === 'LR' ? 'TB' : 'LR';
    setLayoutDirection(next);
    autoLayout(next);
  }, [autoLayout, layoutDirection]);

  const toggleExpanded = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    if (next) {
      panelsBeforeExpand.current = { palette: paletteOpen, inspector: inspectorOpen };
      setPaletteOpen(false);
      setInspectorOpen(false);
    } else if (panelsBeforeExpand.current) {
      setPaletteOpen(panelsBeforeExpand.current.palette);
      setInspectorOpen(panelsBeforeExpand.current.inspector);
      panelsBeforeExpand.current = null;
    }
    // The canvas has just changed size by a few hundred pixels; refitting is
    // what makes the extra room actually show more of the workflow.
    window.setTimeout(() => rfInstance?.fitView({ padding: 0.15, duration: 300 }), 60);
  }, [expanded, inspectorOpen, paletteOpen, rfInstance]);

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

  // Notes are canvas annotations, not workflow steps — the step count, the
  // empty-canvas prompt, and every "is there anything to run/export/search"
  // gate must all read on real steps only, or a workflow that is really
  // empty except for a note would look non-empty and offer to run nothing.
  const realNodeCount = useMemo(
    () => nodes.filter(node => !isNoteNodeId(node.id)).length,
    [nodes],
  );
  const path = useMemo(() => relatedPath(selectedId, edges), [edges, selectedId]);
  const issueNodes = useMemo(() => new Set(
    (preflight?.issues ?? []).map(issue => issue.node_id).filter(Boolean) as string[],
  ), [preflight]);
  // What kind of work each step does, read from the node-type manifest rather
  // than stored in the YAML — the registry is the source of truth for whether a
  // step calls a model, acts outside the platform, or waits for a person.
  const executionKinds = useMemo(() => new Map(
    manifests.map(manifest => [manifest.type_name, manifest.execution_kind]),
  ), [manifests]);
  // Discover once per set of MCP servers the canvas references.
  const mcpServerIds = useMemo(() => {
    const found = new Set<string>();
    for (const node of nodes) {
      if (node.data.typeName !== 'MCPToolAgent') continue;
      const serverId = node.data.config.server_id;
      if (typeof serverId === 'string' && serverId) found.add(serverId);
    }
    return [...found].sort().join(',');
  }, [nodes]);

  useEffect(() => {
    if (!mcpServerIds) return;
    const ids = mcpServerIds.split(',');
    let cancelled = false;
    Promise.all(
      ids.map(id => api.mcpTools(id).catch(() => ({ tools: [] }))),
    ).then(results => {
      if (cancelled) return;
      const next = new Map<string, string>();
      for (const [index, result] of results.entries()) {
        for (const tool of result.tools ?? []) {
          next.set(`${ids[index]}:${tool.name}`, tool.operation);
        }
      }
      setMcpOperations(next);
    });
    return () => { cancelled = true; };
  }, [mcpServerIds]);

  useEffect(() => {
    api.integrationConnections()
      .then(result => setIntegrationConnections(result.connections))
      .catch(() => setIntegrationConnections([]));
  }, []);

  // Everything the canvas knows about a step that the YAML does not: issue
  // state, execution kind, discovered MCP operation, simulation result. Kept
  // separate from the purely visual pass below so the image export can render
  // the whole workflow with these annotations but without the current
  // selection's fading or the current zoom's level of detail.
  const annotatedNodes = useMemo(() => nodes.map(node => ({
    ...node,
    data: {
      ...node.data,
      downstreamCount: edges.filter(edge => edge.source === node.id).length,
      hasIssue: issueNodes.has(node.id),
      executionKind: executionKinds.get(node.data.typeName),
      mcpOperation: node.data.typeName === 'MCPToolAgent'
        ? mcpOperations.get(
            `${String(node.data.config.server_id ?? '')}:${String(node.data.config.tool ?? '')}`,
          )
        : undefined,
      connectionIssue: node.data.typeName === 'IntegrationAgent'
        && integrationConnections.find(item => item.id === node.data.config.connection)?.needs_reauth
        ? ('reauth_required' as const)
        : undefined,
      simulationState: simulationWaiting.has(node.id)
        ? ('waiting' as const)
        : simulationPath.has(node.id)
          ? ('ran' as const)
          : undefined,
    },
  })), [
    edges,
    executionKinds,
    integrationConnections,
    issueNodes,
    mcpOperations,
    nodes,
    simulationPath,
    simulationWaiting,
  ]);

  // Stages are read off the positions currently on the canvas, not off the last
  // dagre run, so hand-dragging a step moves it between columns as you'd expect.
  // Notes are not workflow steps, so they never form or join a stage.
  const stages = useMemo(
    () => (showStages ? groupIntoStages(nodes.filter(node => !isNoteNodeId(node.id)), layoutDirection) : []),
    [layoutDirection, nodes, showStages],
  );
  const stageForNode = useMemo(() => {
    const map = new Map<string, number>();
    for (const stage of stages) {
      for (const id of stage.nodeIds) map.set(id, stage.index);
    }
    return map;
  }, [stages]);

  const collapseStage = useCallback((stageIndex: number) => {
    setCollapsedStages(current => new Set(current).add(stageIndex));
  }, []);

  // Collapsing or expanding everything at once changes the graph's footprint
  // wholesale, so the view refits — collapsing one stage deliberately does not,
  // since that would move everything else out from under the reader.
  const setAllStagesCollapsed = useCallback((collapsed: boolean) => {
    setCollapsedStages(collapsed ? new Set(collapsibleStageIndexes(stages)) : new Set());
    window.setTimeout(() => rfInstance?.fitView({ padding: 0.18, duration: 320 }), 40);
  }, [rfInstance, stages]);
  const expandStage = useCallback((stageIndex: number) => {
    setCollapsedStages(current => {
      const next = new Set(current);
      next.delete(stageIndex);
      return next;
    });
  }, []);

  /** Bring a step into view, optionally zooming in far enough to read it. */
  const focusNode = useCallback((nodeId: string, options: { zoomIn?: boolean } = {}) => {
    const node = nodes.find(item => item.id === nodeId);
    if (!node || !rfInstance) return;
    rfInstance.setCenter(
      node.position.x + (node.width ?? DEFAULT_NODE_WIDTH) / 2,
      node.position.y + (node.height ?? DEFAULT_NODE_HEIGHT) / 2,
      {
        zoom: options.zoomIn
          ? Math.max(rfInstance.getZoom(), FOCUS_MIN_ZOOM)
          : rfInstance.getZoom(),
        duration: 260,
      },
    );
  }, [nodes, rfInstance]);

  const selectNode = useCallback((nodeId: string, options: { zoomIn?: boolean } = {}) => {
    // A step inside a collapsed stage cannot be looked at while it is hidden,
    // so asking to go there opens the stage.
    const stageIndex = stageForNode.get(nodeId);
    if (stageIndex != null && collapsedStages.has(stageIndex)) expandStage(stageIndex);
    setSelectedId(nodeId);
    setShowInputs(false);
    setInspectorTab('configure');
    focusNode(nodeId, options);
  }, [collapsedStages, expandStage, focusNode, stageForNode]);

  const displayNodes = useMemo(() => annotatedNodes.map(node => {
    const isNote = isNoteNodeId(node.id);
    return {
      ...node,
      // The Builder's own `selectedId` is the single source of truth for what is
      // selected, so a step reached by keyboard or by search is highlighted the
      // same as one that was clicked — React Flow would otherwise only mark the
      // ones its own pointer handling selected. A note is never addressed by
      // `selectedId` (it has no Inspector tab), so it keeps react-flow's own
      // native click-to-select instead — that's what makes Delete/Backspace
      // work on it.
      selected: isNote ? Boolean(node.selected) : node.id === selectedId,
      sourcePosition: layoutDirection === 'TB' ? Position.Bottom : Position.Right,
      targetPosition: layoutDirection === 'TB' ? Position.Top : Position.Left,
      data: {
        ...node.data,
        // A note is context for the author, not a step on the graph's
        // critical path — it never dims, regardless of what's selected.
        faded: !isNote && selectedId != null && !isNoteNodeId(selectedId ?? '') && !path.has(node.id),
        compact: detailTier === 'compact',
        flowDirection: layoutDirection,
        ...(isNote ? {
          onNoteChange: (text: string) => updateNoteText(node.id, text),
          onNoteDelete: () => onNodesChange([{ type: 'remove', id: node.id }]),
        } : {
          onNodeDelete: () => onNodesChange([{ type: 'remove', id: node.id }]),
        }),
      },
    };
  }), [annotatedNodes, detailTier, layoutDirection, onNodesChange, path, selectedId, updateNoteText]);
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

  // Collapsed stages fold away first, then the surviving columns get their
  // background bands — a collapsed stage is already one box, so it needs none.
  const collapsed = useMemo(
    () => applyStageCollapse(displayNodes, displayEdges, stages, collapsedStages, expandStage),
    [collapsedStages, displayEdges, displayNodes, expandStage, stages],
  );
  const canvasNodes = useMemo(
    () => (showStages
      ? [...buildStageBandNodes(stages, collapsedStages, collapseStage), ...collapsed.nodes]
      : collapsed.nodes),
    [collapseStage, collapsed.nodes, collapsedStages, showStages, stages],
  );
  const collapsibleStages = useMemo(() => collapsibleStageIndexes(stages), [stages]);

  const runExport = useCallback(async (format: 'png' | 'svg') => {
    if (!meta || annotatedNodes.length === 0) return;
    setExportOpen(false);
    setExporting(format);
    setError(null);
    try {
      // The whole workflow, every step expanded, whatever the canvas is
      // currently showing — an image of a partially collapsed graph would be a
      // record of a viewing session rather than of the workflow. Notes are a
      // personal annotation, not part of the workflow being documented, so
      // they're left out of the exported diagram.
      const exportNodes = annotatedNodes.filter(node => !isNoteNodeId(node.id));
      const image = buildWorkflowSvg({
        title: meta.name,
        subtitle: [
          `${exportNodes.length} steps`,
          `${edges.length} connections`,
          `workflow v${meta.version ?? '1.0'}`,
          workflowName ? `${workflowName}.yaml` : 'unsaved draft',
        ].join(' · '),
        nodes: exportNodes,
        edges,
        stages: showStages ? groupIntoStages(nodes.filter(node => !isNoteNodeId(node.id)), layoutDirection) : [],
        direction: layoutDirection,
      });
      const filename = exportFileName(workflowName, meta.name, format);
      if (format === 'svg') {
        downloadBlob(new Blob([image.svg], { type: 'image/svg+xml;charset=utf-8' }), filename);
      } else {
        downloadBlob(await svgToPngBlob(image), filename);
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setExporting(null);
    }
  }, [annotatedNodes, edges, layoutDirection, meta, nodes, showStages, workflowName]);

  // Keyboard travel across the graph. Scoped to the canvas (and to nothing
  // being focused) so arrow keys still scroll the palette and move a caret
  // inside the Inspector's fields.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = Boolean(target && (
        target.isContentEditable
        || /^(input|textarea|select)$/i.test(target.tagName)
      ));
      if (typing) return;

      if ((event.key === 'k' || event.key === 'K') && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setSearchOpen(true);
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === 'Escape') {
        if (searchOpen) return; // the palette closes itself
        if (exportOpen) setExportOpen(false);
        else if (expanded) toggleExpanded();
        else if (selectedId) setSelectedId(null);
        return;
      }

      const direction = ARROW_DIRECTIONS[event.key];
      if (!direction) return;
      const inCanvas = target === document.body || Boolean(canvasRef.current?.contains(target));
      if (!inCanvas || searchOpen) return;
      event.preventDefault();
      // React Flow's own arrow-key handling (moving the selected node) is off —
      // see disableKeyboardA11y on the canvas — so this is the only listener.
      const nextId = resolveArrowTarget(nodes, edges, selectedId, direction);
      if (nextId) selectNode(nextId);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [edges, expanded, exportOpen, nodes, searchOpen, selectNode, selectedId, toggleExpanded]);

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
    <div className={`builder-shell flex h-full min-h-0 flex-col${expanded ? ' builder-shell--expanded' : ''}`}>
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
              <span
                className={`builder-save-indicator ${dirty ? 'builder-save-indicator--dirty' : 'builder-save-indicator--saved'}`}
                title={autosaveState === 'error' && autosaveError ? autosaveError : undefined}
              >
                {autosaveLabel}
              </span>
            </div>
            <div className="mt-0.5 hidden text-[10px] text-ink-500 sm:block">
              {realNodeCount} nodes · {edges.length} connections · workflow v{meta.version ?? '1.0'}
              {workflowName ? ` · ${workflowName}.yaml` : ''}
            </div>
          </div>
        </div>

        <div className="builder-actionbar-actions" role="toolbar" aria-label="Workflow Builder actions">
          <span className="inline-flex items-center gap-1">
            <button aria-label="Undo" className="ui-icon-button" disabled={past.length === 0} onClick={undo} title="Undo" type="button"><Icon name="undo" size={15} /></button>
            <button aria-label="Redo" className="ui-icon-button" disabled={future.length === 0} onClick={redo} title="Redo" type="button"><Icon name="redo" size={15} /></button>
            <InfoPopover feature="undo_redo" />
          </span>
          <span className="builder-toolbar-separator" />
          <span className="inline-flex items-center gap-1">
            <button className="ui-button ui-button--secondary" onClick={() => { setShowInputs(true); setInspectorOpen(true); }} type="button">
              Inputs <span className="builder-action-count">{Object.keys(meta.inputs ?? {}).length}</span>
            </button>
            <InfoPopover feature="inputs" />
          </span>
          <span className="inline-flex items-center gap-1">
            <button
              className="ui-button ui-button--secondary"
              onClick={() => autoLayout()}
              title={`Arrange ${layoutDirection === 'LR' ? 'left to right' : 'top to bottom'}. Manual positions remain stable until you use this action.`}
              type="button"
            >
              Auto-layout
            </button>
            <InfoPopover feature="auto_layout" />
          </span>
          {workflowName && (
            <span className="inline-flex items-center gap-1">
              <button className="ui-button ui-button--secondary" onClick={() => setVersionHistoryOpen(true)} type="button"><Icon name="history" size={14} /> Versions</button>
              <InfoPopover feature="versions" />
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <button className="ui-button ui-button--secondary" disabled={validating} onClick={() => { setShowInputs(false); setInspectorOpen(true); setInspectorTab('checks'); void validate(); }} type="button">
              <Icon name="check" size={14} /> {validating ? 'Checking…' : 'Preflight'}
            </button>
            <InfoPopover feature="preflight" />
          </span>
          {preflight && !preflight.valid && (
            <button className="ui-button ui-button--secondary" disabled={autofixing} onClick={() => void autofix()} type="button">
              <Icon name="check" size={14} /> {autofixing ? 'Fixing…' : 'Auto-fix'}
            </button>
          )}
          <span className="inline-flex items-center gap-1">
            <button className="ui-button ui-button--secondary" disabled={realNodeCount === 0} onClick={() => void prepareRun(currentWorkflow, 'Full workflow')} type="button"><Icon name="play" size={14} /> Run in Cockpit</button>
            <InfoPopover feature="run_test" />
          </span>
          <span className="inline-flex items-center gap-1">
            <button className="ui-button ui-button--primary" disabled={saveState === 'saving'} onClick={() => void onSave()} type="button"><Icon name="save" size={14} /> {saveState === 'saving' ? 'Saving…' : 'Save'}</button>
            <InfoPopover feature="save" />
          </span>
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
            // Selection travel is owned by the Builder's own arrow-key handler
            // (see the keydown effect): React Flow's built-in handling moves the
            // selected node instead, which would fight it key for key.
            disableKeyboardA11y
            edges={collapsed.edges}
            fitView
            maxZoom={1.8}
            minZoom={0.08}
            nodeTypes={nodeTypes}
            nodes={canvasNodes}
            onConnect={onConnect}
            onEdgesChange={onEdgesChange}
            onInit={instance => {
              setRfInstance(instance);
              setZoom(instance.getZoom());
              if (pendingViewport.current) {
                const viewport = pendingViewport.current;
                pendingViewport.current = null;
                requestAnimationFrame(() => instance.setViewport(viewport, { duration: 0 }));
              }
            }}
            onMove={(_, viewport) => {
              setZoom(viewport.zoom);
              setDetailTier(current => {
                if (current === 'detail' && viewport.zoom < COMPACT_ENTER_ZOOM) return 'compact';
                if (current === 'compact' && viewport.zoom > COMPACT_EXIT_ZOOM) return 'detail';
                return current;
              });
            }}
            onNodeClick={(_, node) => {
              // Stage bands and collapsed-stage placeholders are drawn by the
              // Builder, not part of the workflow — they carry their own controls.
              if (isSyntheticNodeId(node.id)) return;
              // A note has no Inspector tab — selecting it for react-flow's own
              // click-to-select (see displayNodes) must not also drive the
              // single-selectedId Inspector state.
              if (isNoteNodeId(node.id)) return;
              setSelectedId(node.id);
              setShowInputs(false);
              setInspectorOpen(true);
              setInspectorTab('configure');
              if (window.innerWidth <= 900) setPaletteOpen(false);
            }}
            onNodesChange={onNodesChange}
            onPaneClick={() => { setSelectedId(null); setExportOpen(false); }}
          >
            <Background color="var(--border-default)" gap={22} size={1} />
            {/* Bottom-left, because the minimap and the status bar both want the
                bottom-right corner and the zoom controls were losing it. */}
            <Controls position="bottom-left" />
            <MiniMap
              className="builder-minimap"
              maskColor="rgba(242, 251, 250, 0.76)"
              nodeColor={node => (isSyntheticNodeId(node.id) ? 'transparent' : 'var(--brand-teal-600)')}
              onNodeClick={(_, node) => { if (!isSyntheticNodeId(node.id)) selectNode(node.id, { zoomIn: true }); }}
              pannable
              zoomable
            />
          </ReactFlow>

          {/* View controls live on the canvas rather than in the action bar:
              they change how the workflow is read, not what it is. */}
          <div className="builder-canvas-tools" role="toolbar" aria-label="Canvas view controls">
            <span className="builder-canvas-tool inline-flex items-center gap-1 !p-0.5">
              <InfoPopover feature="canvas_basics" />
            </span>
            <button
              className="builder-canvas-tool"
              disabled={realNodeCount === 0}
              onClick={() => setSearchOpen(true)}
              title="Find a step by name (⌘K)"
              type="button"
            >
              <Icon name="search" size={14} /> Find
            </button>
            <button
              className="builder-canvas-tool"
              onClick={addNote}
              title="Add a yellow sticky note to the canvas — a personal annotation, never sent to the workflow"
              type="button"
            >
              <Icon name="note" size={14} /> Add Note
            </button>
            <button
              aria-pressed={showStages}
              className={`builder-canvas-tool${showStages ? ' builder-canvas-tool--on' : ''}`}
              disabled={realNodeCount === 0}
              onClick={() => { setShowStages(value => !value); setCollapsedStages(new Set()); }}
              title="Group the canvas into stage columns, so parallel branches read as one stage"
              type="button"
            >
              <Icon name="columns" size={14} /> Stages
            </button>
            {showStages && collapsibleStages.length > 0 && (
              collapsedStages.size > 0 ? (
                <button className="builder-canvas-tool" onClick={() => setAllStagesCollapsed(false)} title="Expand every collapsed stage" type="button">
                  Expand all
                </button>
              ) : (
                <button className="builder-canvas-tool" onClick={() => setAllStagesCollapsed(true)} title="Collapse every stage that holds parallel steps" type="button">
                  Collapse all
                </button>
              )
            )}
            <button
              className="builder-canvas-tool"
              onClick={toggleLayoutDirection}
              title={layoutDirection === 'LR' ? 'Re-arrange the workflow top-down' : 'Re-arrange the workflow left-to-right'}
              type="button"
            >
              <Icon name={layoutDirection === 'LR' ? 'flow-horizontal' : 'flow-vertical'} size={14} />
              {layoutDirection === 'LR' ? 'Left → right' : 'Top → down'}
            </button>
            <div className="builder-canvas-tool-group">
              <button
                className="builder-canvas-tool"
                disabled={realNodeCount === 0 || exporting !== null}
                onClick={() => setExportOpen(value => !value)}
                title="Export the whole workflow as an image"
                type="button"
              >
                <Icon name="image" size={14} />
                {exporting ? `Exporting ${exporting.toUpperCase()}…` : 'Export'}
              </button>
              {exportOpen && (
                <div className="builder-canvas-menu">
                  <button onClick={() => void runExport('png')} type="button">
                    PNG image
                    <span>Every step, rendered at 3× for slides and documents</span>
                  </button>
                  <button onClick={() => void runExport('svg')} type="button">
                    SVG vector
                    <span>Stays sharp at any zoom; opens in design tools</span>
                  </button>
                </div>
              )}
            </div>
            <button
              aria-pressed={expanded}
              className={`builder-canvas-tool${expanded ? ' builder-canvas-tool--on' : ''}`}
              onClick={toggleExpanded}
              title={expanded ? 'Leave the expanded canvas (Esc)' : 'Fill the window with the canvas'}
              type="button"
            >
              <Icon name={expanded ? 'collapse' : 'expand'} size={14} />
              {expanded ? 'Exit' : 'Expand'}
            </button>
          </div>

          <div className="builder-canvas-status">
            <span>{Math.round(zoom * 100)}%</span>
            {detailTier === 'compact' && <span title="Zoomed out: steps show their name only">Overview</span>}
            <span className="builder-canvas-hint" title="← → follow the connections; ↑ ↓ move between parallel branches">
              ←→ steps · ↑↓ branches
            </span>
            <button onClick={() => rfInstance?.fitView({ padding: 0.2, duration: 300 })} type="button">Fit workflow</button>
            {selectedId && <button onClick={() => focusNode(selectedId, { zoomIn: true })} type="button">Focus step</button>}
            {selectedId && <button onClick={() => setSelectedId(null)} type="button">Clear focus</button>}
          </div>

          {realNodeCount === 0 && (
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
          <aside
            className={inspectorWide ? 'builder-inspector builder-inspector--wide' : 'builder-inspector'}
            aria-label="Builder inspector"
          >
            <BuilderInspector
              edges={edges}
              llmModels={llmModels}
              manifests={manifests}
              nodes={nodes}
              onAutofix={() => void autofix()}
              autofixing={autofixing}
              onClose={() => setInspectorOpen(false)}
              onCloseInputs={() => setShowInputs(false)}
              onToggleWide={() => setInspectorWide(value => !value)}
              wide={inspectorWide}
              onConfigChange={onConfigChange}
              onExperienceChange={onExperienceChange}
              onIdChange={onIdChange}
              onInputsChange={onInputsChange}
              onLaunchTest={(workflow, title) => void prepareRun(workflow, title)}
              onModelRoutingChange={onModelRoutingChange}
              onModelSelectionChange={onModelSelectionChange}
              onNodeRunOutput={recordNodeOutput}
              nodeRunOutputs={nodeRunOutputs}
              onRunWorkflow={() => void prepareRun(currentWorkflow, 'Full workflow')}
              onHighlightPath={(executed, waiting) => {
                setSimulationPath(new Set(executed));
                setSimulationWaiting(new Set(waiting));
              }}
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
              workflowYaml={currentYaml}
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

      {searchOpen && (
        <NodeSearchPalette
          nodes={annotatedNodes.filter(node => !isNoteNodeId(node.id))}
          onClose={() => setSearchOpen(false)}
          onSelect={nodeId => {
            setSearchOpen(false);
            selectNode(nodeId, { zoomIn: true });
          }}
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
