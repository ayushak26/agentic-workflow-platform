import yaml from 'js-yaml';
import type { Node as RFNode, Edge as RFEdge } from 'reactflow';

// What's in a YAML workflow, parsed shape
export type WorkflowInputSpec = {
  type: 'file' | 'text' | 'json';
  description?: string;
  required?: boolean;
  multiple?: boolean;
  accept?: string[];
  max_files?: number;
};

export type ModelRoutingPolicy = {
  accuracy_priority?: 'maximum' | 'balanced' | 'economy';
  max_estimated_cost_usd?: number | null;
  prefer_low_latency?: boolean;
  quality_scores?: Record<string, number>;
};

export type YamlWorkflowNode = {
  id: string;
  type: string;
  config?: Record<string, unknown>;
  allowed_models?: string[];
  selected_model?: string | null;
  model_routing?: ModelRoutingPolicy;
};

export type YamlWorkflow = {
  name: string;
  description?: string;
  version?: string;
  use_case?: string;
  inputs?: Record<string, WorkflowInputSpec>;   // was Record<string, unknown>
  static_variables?: Array<{ name: string; type: string; value: unknown }>;
  nodes: YamlWorkflowNode[];
  edges: Array<{
    from: string;
    to?: string | string[];
    condition?: string;
    branches?: Record<string, string>;
  }>;
  entry?: string;
  exit?: string | string[];
  output?: Record<string, unknown>;
};

// Custom data we hang on each React Flow node
export type WorkflowNodeData = {
  nodeId: string;        // the YAML node id
  typeName: string;      // the registered type_name
  config: Record<string, unknown>;
  allowedModels?: string[];
  selectedModel?: string | null;
  modelRouting?: ModelRoutingPolicy;
};

const NODE_X = 320;
const NODE_Y_GAP = 120;

export function parseYaml(text: string): YamlWorkflow {
  return yaml.load(text) as YamlWorkflow;
}

export function dumpYaml(wf: YamlWorkflow): string {
  return yaml.dump(wf, { noRefs: true, lineWidth: 100 });
}

/** YAML → React Flow nodes + edges. Auto-positions vertically by declaration order. */
export function yamlToReactFlow(
  wf: YamlWorkflow
): { nodes: RFNode<WorkflowNodeData>[]; edges: RFEdge[] } {
  const nodes: RFNode<WorkflowNodeData>[] = wf.nodes.map((n, i) => ({
    id: n.id,
    type: 'workflow',                       // matches the key in `nodeTypes` prop
    position: { x: NODE_X, y: i * NODE_Y_GAP },
    data: {
      nodeId: n.id,
      typeName: n.type,
      config: n.config ?? {},
      allowedModels: n.allowed_models,
      selectedModel: n.selected_model,
      modelRouting: n.model_routing,
    },
  }));

  const edges: RFEdge[] = [];
  let edgeId = 0;
  for (const e of wf.edges) {
    // Conditional router edges: one source, multiple labeled branches.
    if (e.branches) {
      for (const [label, target] of Object.entries(e.branches)) {
        edges.push({
          id: `e${edgeId++}`,
          source: e.from,
          target,
          label,
        });
      }
      continue;
    }
    // Fan-out: one source, multiple targets via list.
    if (Array.isArray(e.to)) {
      for (const target of e.to) {
        edges.push({ id: `e${edgeId++}`, source: e.from, target });
      }
      continue;
    }
    // Simple edge.
    if (e.to) {
      edges.push({ id: `e${edgeId++}`, source: e.from, target: e.to });
    }
  }
  return { nodes, edges };
}

/** React Flow nodes + edges → YAML workflow (used in 9B.2b for save). */
export function reactFlowToYaml(
  meta: Omit<YamlWorkflow, 'nodes' | 'edges'>,
  rfNodes: RFNode<WorkflowNodeData>[],
  rfEdges: RFEdge[]
): YamlWorkflow {
  const nodes: YamlWorkflowNode[] = rfNodes.map(n => ({
    id: n.data.nodeId,
    type: n.data.typeName,
    config: n.data.config,
    ...(n.data.allowedModels
      ? { allowed_models: n.data.allowedModels }
      : {}),
    ...(n.data.selectedModel
      ? { selected_model: n.data.selectedModel }
      : {}),
    ...(n.data.modelRouting
      ? { model_routing: n.data.modelRouting }
      : {}),
  }));

  // Group edges by source. Multiple targets become a list.
  const bySource = new Map<string, string[]>();
  for (const e of rfEdges) {
    const arr = bySource.get(e.source) ?? [];
    arr.push(e.target);
    bySource.set(e.source, arr);
  }
  const edges = Array.from(bySource.entries()).map(([from, targets]) => ({
    from,
    to: targets.length === 1 ? targets[0] : targets,
  }));

  return {
    ...meta,
    version: meta.version ?? '1.0',
    inputs: meta.inputs ?? {},
    nodes,
    edges,
  };
}
