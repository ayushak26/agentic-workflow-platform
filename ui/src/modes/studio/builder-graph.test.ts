import { describe, expect, it } from 'vitest';
import type { Node } from 'reactflow';
import type { NodeTypeManifest } from '../../api/types';
import { manifestFixture } from './builder/test-fixtures';
import {
  buildVariableOptions,
  outgoingEdges,
  renameNodeReferencesInConfig,
  sliceWorkflowThroughBranch,
  sliceWorkflowThroughNode,
  upstreamNodeIds,
} from './builder-graph';
import type { WorkflowEdgeData, WorkflowNodeData, YamlWorkflow } from './yaml-bridge';

function node(id: string, typeName: string): Node<WorkflowNodeData> {
  return {
    id,
    type: 'workflow',
    position: { x: 0, y: 0 },
    data: { nodeId: id, typeName, config: {} },
  };
}

function edge(
  source: string,
  target: string,
  extra: Partial<WorkflowEdgeData> = {},
): { id: string; source: string; target: string; data: WorkflowEdgeData } {
  return {
    id: `${source}->${target}`,
    source,
    target,
    data: { edgeKind: 'simple', groupId: `${source}->${target}`, ...extra },
  };
}

describe('upstreamNodeIds', () => {
  it('finds every ancestor of a diamond graph, not just direct parents', () => {
    // a -> b -> d, a -> c -> d
    const edges = [edge('a', 'b'), edge('a', 'c'), edge('b', 'd'), edge('c', 'd')];
    const upstream = upstreamNodeIds('d', edges);
    expect(upstream).toEqual(new Set(['a', 'b', 'c']));
  });

  it('is empty for a root node', () => {
    const edges = [edge('a', 'b')];
    expect(upstreamNodeIds('a', edges)).toEqual(new Set());
  });
});

describe('buildVariableOptions', () => {
  const manifests: NodeTypeManifest[] = [
    manifestFixture({
      type_name: 'Fetcher',
      output_schema: { properties: { text: {}, score: {} } },
    }),
    manifestFixture({ type_name: 'Blank' }),
  ];

  it('excludes sibling and downstream nodes, only including upstream ones', () => {
    const nodes = [node('a', 'Fetcher'), node('b', 'Fetcher'), node('target', 'Fetcher'), node('sibling', 'Fetcher')];
    const edges = [edge('a', 'target'), edge('a', 'sibling')];
    const options = buildVariableOptions({ inputs: {}, static_variables: [] }, 'target', nodes, edges, manifests);
    const sourceIds = new Set(options.map(o => o.sourceNodeId).filter(Boolean));
    expect(sourceIds).toEqual(new Set(['a']));
    expect(sourceIds.has('sibling')).toBe(false);
    expect(sourceIds.has('b')).toBe(false);
  });

  it('expands declared output_schema fields into one token each', () => {
    const nodes = [node('a', 'Fetcher'), node('target', 'Fetcher')];
    const edges = [edge('a', 'target')];
    const options = buildVariableOptions({ inputs: {}, static_variables: [] }, 'target', nodes, edges, manifests);
    const tokens = options.filter(o => o.sourceNodeId === 'a').map(o => o.token);
    expect(new Set(tokens)).toEqual(new Set(['{{a.text}}', '{{a.score}}']));
  });

  it('falls back to a bare outputs token when a node has no declared output fields', () => {
    const nodes = [node('a', 'Blank'), node('target', 'Fetcher')];
    const edges = [edge('a', 'target')];
    const options = buildVariableOptions({ inputs: {}, static_variables: [] }, 'target', nodes, edges, manifests);
    const fromA = options.filter(o => o.sourceNodeId === 'a');
    expect(fromA).toHaveLength(1);
    expect(fromA[0].token).toBe('{{outputs.a}}');
  });

  it('includes workflow inputs and static variables regardless of graph position', () => {
    const nodes = [node('target', 'Fetcher')];
    const options = buildVariableOptions(
      {
        inputs: { doc: { type: 'text', description: 'The document' } },
        static_variables: [{ name: 'policy', type: 'text', value: 'strict' }],
      },
      'target',
      nodes,
      [],
      manifests,
    );
    expect(options.some(o => o.token === '{{inputs.doc}}')).toBe(true);
    expect(options.some(o => o.token === '{{variables.policy}}')).toBe(true);
  });
});

function routerWorkflow(): YamlWorkflow {
  return {
    name: 'Router workflow',
    version: '1.0',
    nodes: [
      { id: 'source', type: 'Literal', config: { value: 1 } },
      { id: 'route', type: 'RouterAgent', config: {} },
      { id: 'approve', type: 'Literal', config: { value: 'ok' } },
      { id: 'reject', type: 'Literal', config: { value: 'no' } },
      { id: 'after_approve', type: 'Literal', config: { value: 'done' } },
    ],
    edges: [
      { from: 'source', to: 'route' },
      { from: 'route', branches: { approve: 'approve', reject: 'reject' } },
      { from: 'approve', to: 'after_approve' },
    ],
  };
}

describe('sliceWorkflowThroughNode', () => {
  it('keeps only ancestors and stops output at the target node', () => {
    const sliced = sliceWorkflowThroughNode(routerWorkflow(), 'route');
    const ids = new Set(sliced.nodes.map(n => n.id));
    expect(ids).toEqual(new Set(['source', 'route']));
    expect(sliced.exit).toBe('route');
    expect(sliced.output?.nodes).toEqual([{ node_id: 'route', flatten: true }]);
  });

  it('never mutates the original workflow object', () => {
    const original = routerWorkflow();
    const before = JSON.stringify(original);
    sliceWorkflowThroughNode(original, 'route');
    expect(JSON.stringify(original)).toBe(before);
  });
});

describe('sliceWorkflowThroughBranch', () => {
  it('drops the sibling branch and forces the retained route deterministically', () => {
    const sliced = sliceWorkflowThroughBranch(routerWorkflow(), 'route', 'approve', 'approve');
    const ids = new Set(sliced.nodes.map(n => n.id));
    expect(ids.has('reject')).toBe(false);
    expect(ids).toEqual(new Set(['source', 'route', 'approve', 'after_approve']));

    const routerEdge = sliced.edges.find(e => e.from === 'route')!;
    expect(routerEdge.branches).toEqual({ approve: 'approve' });

    const routerNode = sliced.nodes.find(n => n.id === 'route')!;
    expect(routerNode.config).toMatchObject({
      mode: 'rule',
      rules: [{ name: 'approve', default: true }],
    });
  });

  it('keeps a fan-out edge intact when both targets survive the slice', () => {
    const workflow: YamlWorkflow = {
      name: 'Fan-out',
      version: '1.0',
      nodes: [
        { id: 'a', type: 'Literal', config: {} },
        { id: 'b', type: 'Literal', config: {} },
        { id: 'c', type: 'Literal', config: {} },
      ],
      edges: [{ from: 'a', to: ['b', 'c'] }],
    };
    const sliced = sliceWorkflowThroughBranch(workflow, 'a', 'b');
    const fanOutEdge = sliced.edges.find(e => e.from === 'a')!;
    expect(fanOutEdge.to).toBe('b');
  });
});

describe('outgoingEdges', () => {
  it('returns only edges sourced from the given node', () => {
    const edges = [edge('a', 'b'), edge('a', 'c'), edge('b', 'c')];
    expect(outgoingEdges('a', edges).map(e => e.target).sort()).toEqual(['b', 'c']);
    expect(outgoingEdges('c', edges)).toEqual([]);
  });
});

describe('renameNodeReferencesInConfig', () => {
  it('rewrites a {{old_id.field}} template token to the new id', () => {
    const config = { prompt_template: 'Signals: {{search_for_signals.results}}' };
    const renamed = renameNodeReferencesInConfig(config, 'search_for_signals', 'search_for_signals_v2');
    expect(renamed.prompt_template).toBe('Signals: {{search_for_signals_v2.results}}');
  });

  it('rewrites a bare {{outputs.old_id}} token', () => {
    const config = { prompt_template: '{{outputs.fetch}} and more' };
    const renamed = renameNodeReferencesInConfig(config, 'fetch', 'fetcher');
    expect(renamed.prompt_template).toBe('{{outputs.fetcher}} and more');
  });

  it('does not touch a longer id that merely starts with the same characters', () => {
    const config = { prompt_template: '{{search_2.value}} vs {{search.value}}' };
    const renamed = renameNodeReferencesInConfig(config, 'search', 'lookup');
    expect(renamed.prompt_template).toBe('{{search_2.value}} vs {{lookup.value}}');
  });

  it('recurses into nested arrays and objects', () => {
    const config = { rules: [{ name: 'a', template: '{{old.x}}' }, { name: 'b', template: 'no ref here' }] };
    const renamed = renameNodeReferencesInConfig(config, 'old', 'new_id') as {
      rules: Array<{ name: string; template: string }>;
    };
    expect(renamed.rules[0].template).toBe('{{new_id.x}}');
    expect(renamed.rules[1].template).toBe('no ref here');
  });

  it('leaves unrelated config untouched', () => {
    const config = { value: 42, flag: true, nested: { text: 'no tokens' } };
    const renamed = renameNodeReferencesInConfig(config, 'anything', 'else');
    expect(renamed).toEqual(config);
  });
});
