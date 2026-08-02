import type { Edge, Node } from 'reactflow';
import type { NodeTypeManifest } from '../../api/types';
import type {
  WorkflowEdgeData,
  WorkflowNodeData,
  YamlWorkflow,
  YamlWorkflowNode,
} from './yaml-bridge';

export type VariableOption = {
  group: 'Workflow inputs' | 'Static variables' | 'Upstream outputs';
  label: string;
  token: string;
  description: string;
  sourceNodeId?: string;
};

type SchemaShape = {
  properties?: Record<string, unknown>;
};

export function schemaFields(schema: Record<string, unknown> | undefined): string[] {
  const properties = (schema as SchemaShape | undefined)?.properties ?? {};
  return Object.keys(properties);
}

// Renaming a node must not silently break every other node's template
// tokens that reference it — `{{old_id.field}}` and `{{outputs.old_id}}`
// both address the node by id, same as an edge or the entry/exit fields.
// `nodeId` is restricted to `^[A-Za-z_][A-Za-z0-9_]*$` (enforced by the
// caller before this runs), so it's safe to interpolate directly into the
// pattern without escaping. The lookahead after the id (a literal `.` or
// the closing `}}`) is what keeps this from matching a *longer* id that
// happens to start with the same characters (e.g. renaming "search" must
// not also rewrite a token addressing "search_2").
function renameTokensInText(text: string, oldId: string, nextId: string): string {
  const pattern = new RegExp(
    `(\\{\\{\\s*(?:outputs\\.)?)${oldId}(?=\\.|\\s*\\}\\})`,
    'g',
  );
  return text.replace(pattern, `$1${nextId}`);
}

export function renameNodeReferencesInValue(
  value: unknown,
  oldId: string,
  nextId: string,
): unknown {
  if (typeof value === 'string') return renameTokensInText(value, oldId, nextId);
  if (Array.isArray(value)) {
    return value.map(item => renameNodeReferencesInValue(item, oldId, nextId));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        renameNodeReferencesInValue(item, oldId, nextId),
      ]),
    );
  }
  return value;
}

export function renameNodeReferencesInConfig(
  config: Record<string, unknown>,
  oldId: string,
  nextId: string,
): Record<string, unknown> {
  return renameNodeReferencesInValue(config, oldId, nextId) as Record<string, unknown>;
}

export function upstreamNodeIds(
  selectedId: string,
  edges: Array<Pick<Edge, 'source' | 'target'>>,
): Set<string> {
  const reverse = new Map<string, string[]>();
  for (const edge of edges) {
    reverse.set(edge.target, [...(reverse.get(edge.target) ?? []), edge.source]);
  }
  const upstream = new Set<string>();
  const queue = [...(reverse.get(selectedId) ?? [])];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (upstream.has(current)) continue;
    upstream.add(current);
    queue.push(...(reverse.get(current) ?? []));
  }
  return upstream;
}

export function buildVariableOptions(
  workflow: Pick<YamlWorkflow, 'inputs' | 'static_variables'>,
  selectedId: string,
  nodes: Node<WorkflowNodeData>[],
  edges: Edge<WorkflowEdgeData>[],
  manifests: NodeTypeManifest[],
): VariableOption[] {
  const options: VariableOption[] = [];
  for (const [name, spec] of Object.entries(workflow.inputs ?? {})) {
    options.push({
      group: 'Workflow inputs',
      label: name,
      token: `{{inputs.${name}}}`,
      description: spec.description || `${spec.type} workflow input`,
    });
  }
  for (const variable of workflow.static_variables ?? []) {
    options.push({
      group: 'Static variables',
      label: variable.name,
      token: `{{variables.${variable.name}}}`,
      description: `${variable.type} static variable`,
    });
  }
  const upstream = upstreamNodeIds(selectedId, edges);
  for (const node of nodes) {
    if (!upstream.has(node.id)) continue;
    const manifest = manifests.find(item => item.type_name === node.data.typeName);
    const fields = schemaFields(manifest?.output_schema);
    if (fields.length === 0) {
      options.push({
        group: 'Upstream outputs',
        label: node.data.nodeId,
        token: `{{outputs.${node.data.nodeId}}}`,
        description: `${node.data.typeName} output`,
        sourceNodeId: node.data.nodeId,
      });
      continue;
    }
    for (const field of fields) {
      options.push({
        group: 'Upstream outputs',
        label: `${node.data.nodeId}.${field}`,
        token: `{{${node.data.nodeId}.${field}}}`,
        description: `${node.data.typeName} · ${field}`,
        sourceNodeId: node.data.nodeId,
      });
    }
  }
  return options;
}

function edgeTargets(edge: YamlWorkflow['edges'][number]): string[] {
  if (edge.branches) return Object.values(edge.branches);
  if (Array.isArray(edge.to)) return edge.to;
  return edge.to ? [edge.to] : [];
}

function ancestors(workflow: YamlWorkflow, target: string): Set<string> {
  const reverse = new Map<string, string[]>();
  for (const edge of workflow.edges) {
    for (const to of edgeTargets(edge)) {
      reverse.set(to, [...(reverse.get(to) ?? []), edge.from]);
    }
  }
  const found = new Set<string>([target]);
  const queue = [...(reverse.get(target) ?? [])];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (found.has(current)) continue;
    found.add(current);
    queue.push(...(reverse.get(current) ?? []));
  }
  return found;
}

function descendants(workflow: YamlWorkflow, source: string): Set<string> {
  const forward = new Map<string, string[]>();
  for (const edge of workflow.edges) {
    forward.set(edge.from, [
      ...(forward.get(edge.from) ?? []),
      ...edgeTargets(edge),
    ]);
  }
  const found = new Set<string>([source]);
  const queue = [...(forward.get(source) ?? [])];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (found.has(current)) continue;
    found.add(current);
    queue.push(...(forward.get(current) ?? []));
  }
  return found;
}

function filterEdges(
  workflow: YamlWorkflow,
  keep: Set<string>,
): YamlWorkflow['edges'] {
  const result: YamlWorkflow['edges'] = [];
  for (const edge of workflow.edges) {
    if (!keep.has(edge.from)) continue;
    if (edge.branches) {
      const branches = Object.fromEntries(
        Object.entries(edge.branches).filter(([, target]) => keep.has(target)),
      );
      if (Object.keys(branches).length > 0) {
        result.push({ ...edge, branches, to: undefined });
      }
      continue;
    }
    const targets = edgeTargets(edge).filter(target => keep.has(target));
    if (targets.length > 0) {
      result.push({
        ...edge,
        to: targets.length === 1 ? targets[0] : targets,
      });
    }
  }
  return result;
}

function forceSingleRouterBranches(
  nodes: YamlWorkflowNode[],
  edges: YamlWorkflow['edges'],
): YamlWorkflowNode[] {
  const singleBranches = new Map<string, string>();
  for (const edge of edges) {
    const labels = Object.keys(edge.branches ?? {});
    if (labels.length === 1) singleBranches.set(edge.from, labels[0]);
  }
  return nodes.map(node => {
    const route = singleBranches.get(node.id);
    if (!route || node.type !== 'RouterAgent') return node;
    return {
      ...node,
      config: {
        ...(node.config ?? {}),
        mode: 'rule',
        rules: [{ name: route, default: true }],
      },
    };
  });
}

function leafIds(nodes: YamlWorkflowNode[], edges: YamlWorkflow['edges']): string[] {
  const sources = new Set(edges.map(edge => edge.from));
  return nodes.map(node => node.id).filter(id => !sources.has(id));
}

function testWorkflow(
  workflow: YamlWorkflow,
  keep: Set<string>,
  nameSuffix: string,
  edges = filterEdges(workflow, keep),
): YamlWorkflow {
  let nodes = workflow.nodes.filter(node => keep.has(node.id));
  nodes = forceSingleRouterBranches(nodes, edges);
  const nodeIds = new Set(nodes.map(node => node.id));
  const roots = nodes
    .map(node => node.id)
    .filter(id => !edges.some(edge => edgeTargets(edge).includes(id)));
  const leaves = leafIds(nodes, edges);
  const entry = workflow.entry && nodeIds.has(workflow.entry)
    ? workflow.entry
    : roots[0];
  return {
    ...workflow,
    name: `${workflow.name} · ${nameSuffix}`,
    nodes,
    edges,
    entry,
    exit: leaves.length === 1 ? leaves[0] : leaves,
    output: {
      include_input: false,
      nodes: leaves.map(node_id => ({ node_id, flatten: true })),
    },
  };
}

export function sliceWorkflowThroughNode(
  workflow: YamlWorkflow,
  nodeId: string,
): YamlWorkflow {
  const keep = ancestors(workflow, nodeId);
  const sliced = testWorkflow(workflow, keep, `test ${nodeId}`);
  return {
    ...sliced,
    exit: nodeId,
    output: {
      include_input: false,
      nodes: [{ node_id: nodeId, flatten: true }],
    },
  };
}

export function sliceWorkflowThroughBranch(
  workflow: YamlWorkflow,
  sourceId: string,
  targetId: string,
  branchLabel?: string,
): YamlWorkflow {
  const keep = new Set([
    ...ancestors(workflow, sourceId),
    ...descendants(workflow, targetId),
  ]);
  let edges = filterEdges(workflow, keep);
  edges = edges.flatMap(edge => {
    if (edge.from !== sourceId) return [edge];
    if (edge.branches) {
      const match = Object.entries(edge.branches).find(([label, target]) => (
        target === targetId && (!branchLabel || label === branchLabel)
      ));
      return match
        ? [{ ...edge, branches: { [match[0]]: match[1] }, to: undefined }]
        : [];
    }
    const targets = edgeTargets(edge).filter(target => target === targetId);
    return targets.length > 0
      ? [{ ...edge, to: targetId }]
      : [];
  });
  return testWorkflow(
    workflow,
    keep,
    `test ${sourceId} → ${branchLabel ?? targetId}`,
    edges,
  );
}

export function outgoingEdges(
  nodeId: string,
  edges: Edge<WorkflowEdgeData>[],
): Edge<WorkflowEdgeData>[] {
  return edges.filter(edge => edge.source === nodeId);
}
