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

// Values copied from Run History's "Copy run as workflow inputs" (or a
// pipeline stage's auto-matched output) often carry the TransformAgent
// envelope ({ raw, parsed }) rather than the bare structured value a `json`
// input expects. These workflows already document that convention (e.g. an
// input described as "proposal_blueprint.parsed - the locked single source
// of truth"), so unwrap it here rather than requiring a manual `.parsed`
// edit on every reuse. Shared by RunDialog (workflow inputs) and the
// pipeline launch dialog (pipeline inputs) so the heuristic can't drift
// between the two.
export function valueForJsonInput(value: unknown): unknown {
  if (
    value !== null
    && typeof value === 'object'
    && !Array.isArray(value)
    && 'parsed' in (value as Record<string, unknown>)
    && 'raw' in (value as Record<string, unknown>)
  ) {
    return (value as Record<string, unknown>).parsed;
  }
  return value;
}

export type ModelRoutingPolicy = {
  accuracy_priority?: 'maximum' | 'balanced' | 'economy';
  max_estimated_cost_usd?: number | null;
  prefer_low_latency?: boolean;
  quality_scores?: Record<string, number>;
};

// Optional, presentation-only metadata for Guided Run. Mirrors
// app/runtime/schema.py's GuidedStageSpec/WorkflowExperienceSpec/
// NodeExperienceSpec field-for-field — never changes routing or execution.
export type GuidedVisibility = 'standard' | 'summary' | 'advanced';

export type GuidedStageSpec = {
  id: string;
  display_name: string;
  purpose?: string;
  node_ids?: string[];
  success_criteria?: string[];
  expected_output?: string;
  visibility?: GuidedVisibility;
  weight?: number;
  may_ask_questions?: boolean;
  may_require_approval?: boolean;
};

export type WorkflowExperienceSpec = {
  schema_version?: string;
  goal?: string;
  stages?: GuidedStageSpec[];
};

export type NodeExperienceSpec = {
  stage_id?: string;
  display_name?: string;
  purpose?: string;
  contribution?: string;
  expected_output?: string;
  success_condition?: string;
  quality_checks?: string[];
  failure_message?: string;
  recovery_actions?: string[];
  visibility?: GuidedVisibility;
  receiving_steps?: string[];
  handoff_fields?: string[];
  agent_role?: string;
  show_agent_role?: boolean;
};

export type YamlWorkflowNode = {
  id: string;
  type: string;
  config?: Record<string, unknown>;
  allowed_models?: string[];
  selected_model?: string | null;
  model_routing?: ModelRoutingPolicy;
  experience?: NodeExperienceSpec;
};

export type YamlWorkflow = {
  name: string;
  description?: string;
  version?: string;
  use_case?: string;
  experience?: WorkflowExperienceSpec;
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
  experience?: NodeExperienceSpec;
  downstreamCount?: number;
  hasIssue?: boolean;
  faded?: boolean;
  // Presentation only, injected by the Builder from the node-type manifest.
  // Not part of the YAML: the registry is the source of truth for what kind of
  // work a node type does.
  executionKind?:
    | 'ai'
    | 'deterministic'
    | 'external'
    | 'human'
    | 'input'
    | 'output';
  // Set while a simulation's result is on screen, so the canvas shows the path
  // a request actually took and where it is waiting for a person.
  simulationState?: 'ran' | 'waiting';
  // For MCP steps: the discovered operation class of the selected tool, so the
  // canvas shows READ or WRITE without opening the inspector. Discovered from
  // the server, so it is not part of the saved YAML.
  mcpOperation?: string;
};

export type WorkflowEdgeData = {
  edgeKind: 'simple' | 'branch';
  groupId: string;
  condition?: string;
  branchLabel?: string;
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
): { nodes: RFNode<WorkflowNodeData>[]; edges: RFEdge<WorkflowEdgeData>[] } {
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
      experience: n.experience,
    },
  }));

  const edges: RFEdge<WorkflowEdgeData>[] = [];
  for (const [edgeIndex, e] of (wf.edges ?? []).entries()) {
    const groupId = `yaml-edge-${edgeIndex}`;
    // Conditional router edges: one source, multiple labeled branches.
    if (e.branches) {
      for (const [label, target] of Object.entries(e.branches)) {
        edges.push({
          id: `${groupId}-${label}`,
          source: e.from,
          target,
          label,
          data: {
            edgeKind: 'branch',
            groupId,
            condition: e.condition,
            branchLabel: label,
          },
        });
      }
      continue;
    }
    // Fan-out: one source, multiple targets via list.
    if (Array.isArray(e.to)) {
      for (const [targetIndex, target] of e.to.entries()) {
        edges.push({
          id: `${groupId}-${targetIndex}`,
          source: e.from,
          target,
          data: {
            edgeKind: 'simple',
            groupId,
            condition: e.condition,
          },
        });
      }
      continue;
    }
    // Simple edge.
    if (e.to) {
      edges.push({
        id: `${groupId}-0`,
        source: e.from,
        target: e.to,
        data: {
          edgeKind: 'simple',
          groupId,
          condition: e.condition,
        },
      });
    }
  }
  return { nodes, edges };
}

/** React Flow nodes + edges → YAML workflow (used in 9B.2b for save). */
export function reactFlowToYaml(
  meta: Omit<YamlWorkflow, 'nodes' | 'edges'>,
  rfNodes: RFNode<WorkflowNodeData>[],
  rfEdges: RFEdge<WorkflowEdgeData>[]
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
    ...(n.data.experience
      ? { experience: n.data.experience }
      : {}),
  }));

  // Preserve each original YAML edge group, including router branch labels
  // and condition fields. Newly drawn simple edges group by source.
  const groups = new Map<string, RFEdge<WorkflowEdgeData>[]>();
  for (const edge of rfEdges) {
    const key = edge.data?.groupId ?? `new-edge-${edge.source}`;
    const group = groups.get(key) ?? [];
    group.push(edge);
    groups.set(key, group);
  }
  const edges: YamlWorkflow['edges'] = [];
  for (const group of groups.values()) {
    const first = group[0];
    const branchEdges = group.filter(edge => edge.data?.edgeKind === 'branch');
    if (branchEdges.length > 0) {
      edges.push({
        from: first.source,
        condition: first.data?.condition,
        branches: Object.fromEntries(
          branchEdges.map(edge => [
            edge.data?.branchLabel ?? String(edge.label ?? edge.target),
            edge.target,
          ]),
        ),
      });
      continue;
    }
    const targets = group.map(edge => edge.target);
    edges.push({
      from: first.source,
      to: targets.length === 1 ? targets[0] : targets,
      ...(first.data?.condition ? { condition: first.data.condition } : {}),
    });
  }

  return {
    ...meta,
    version: meta.version ?? '1.0',
    inputs: meta.inputs ?? {},
    nodes,
    edges,
  };
}
