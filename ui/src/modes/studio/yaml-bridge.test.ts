import { describe, expect, it } from 'vitest';
import {
  dumpYaml,
  parseYaml,
  reactFlowToYaml,
  yamlToReactFlow,
  type YamlWorkflow,
} from './yaml-bridge';

function roundTrip(workflow: YamlWorkflow): YamlWorkflow {
  const { nodes, edges } = yamlToReactFlow(workflow);
  const { nodes: metaNodes, edges: metaEdges, ...meta } = workflow;
  void metaNodes;
  void metaEdges;
  return parseYaml(dumpYaml(reactFlowToYaml(meta, nodes, edges)));
}

describe('yaml-bridge round trip', () => {
  it('preserves router branches through canvas and back', () => {
    const workflow: YamlWorkflow = {
      name: 'Router workflow',
      version: '1.0',
      nodes: [
        { id: 'route', type: 'RouterAgent', config: {} },
        { id: 'approve', type: 'Literal', config: { value: 'ok' } },
        { id: 'reject', type: 'Literal', config: { value: 'no' } },
      ],
      edges: [
        {
          from: 'route',
          condition: 'result.decision',
          branches: { approve: 'approve', reject: 'reject' },
        },
      ],
    };

    const result = roundTrip(workflow);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].branches).toEqual({ approve: 'approve', reject: 'reject' });
    expect(result.edges[0].condition).toBe('result.decision');
    expect(result.edges[0].to).toBeUndefined();
  });

  it('preserves a fan-out edge through canvas and back', () => {
    const workflow: YamlWorkflow = {
      name: 'Fan-out workflow',
      version: '1.0',
      nodes: [
        { id: 'source', type: 'Literal', config: { value: 1 } },
        { id: 'a', type: 'Literal', config: { value: 2 } },
        { id: 'b', type: 'Literal', config: { value: 3 } },
      ],
      edges: [{ from: 'source', to: ['a', 'b'] }],
    };

    const result = roundTrip(workflow);
    expect(result.edges).toHaveLength(1);
    expect(new Set(result.edges[0].to as string[])).toEqual(new Set(['a', 'b']));
  });

  it('preserves model_routing/selected_model/allowed_models on nodes', () => {
    const workflow: YamlWorkflow = {
      name: 'Model routing workflow',
      version: '1.0',
      nodes: [
        {
          id: 'draft',
          type: 'ConceptAlternativesAgent',
          config: { model: 'auto' },
          allowed_models: ['gpt', 'claude'],
          selected_model: 'auto',
          model_routing: { accuracy_priority: 'maximum', prefer_low_latency: false },
        },
      ],
      edges: [],
    };

    const result = roundTrip(workflow);
    const node = result.nodes[0];
    expect(node.allowed_models).toEqual(['gpt', 'claude']);
    expect(node.selected_model).toBe('auto');
    expect(node.model_routing).toEqual({ accuracy_priority: 'maximum', prefer_low_latency: false });
  });

  it('preserves a single labeled branch with a condition', () => {
    const workflow: YamlWorkflow = {
      name: 'Single branch workflow',
      version: '1.0',
      nodes: [
        { id: 'route', type: 'RouterAgent', config: {} },
        { id: 'only', type: 'Literal', config: { value: 1 } },
      ],
      edges: [
        { from: 'route', condition: 'always', branches: { only: 'only' } },
      ],
    };

    const result = roundTrip(workflow);
    expect(result.edges[0].branches).toEqual({ only: 'only' });
    expect(result.edges[0].condition).toBe('always');
  });

  it('new edges drawn on the canvas get a fresh groupId so they save as their own YAML edge', () => {
    const workflow: YamlWorkflow = {
      name: 'Two sources',
      version: '1.0',
      nodes: [
        { id: 'a', type: 'Literal', config: { value: 1 } },
        { id: 'b', type: 'Literal', config: { value: 2 } },
        { id: 'c', type: 'Literal', config: { value: 3 } },
      ],
      edges: [{ from: 'a', to: 'c' }],
    };
    const { nodes, edges } = yamlToReactFlow(workflow);
    const withNewEdge = [
      ...edges,
      { id: 'new-1', source: 'b', target: 'c', data: { edgeKind: 'simple' as const, groupId: 'canvas-edge-1' } },
    ];
    const { nodes: metaNodes, edges: metaEdges, ...meta } = workflow;
    void metaNodes;
    void metaEdges;
    const result = reactFlowToYaml(meta, nodes, withNewEdge);
    expect(result.edges).toHaveLength(2);
    const targets = result.edges.flatMap(e => (Array.isArray(e.to) ? e.to : e.to ? [e.to] : []));
    expect(new Set(targets)).toEqual(new Set(['c']));
  });

  it('preserves workflow-level and node-level Guided Run experience metadata', () => {
    const workflow: YamlWorkflow = {
      name: 'Guided workflow',
      version: '1.0',
      experience: {
        goal: 'Produce a checked requirement matrix.',
        stages: [
          {
            id: 'understand',
            display_name: 'Understand the request',
            node_ids: ['map_requirements'],
          },
        ],
      },
      nodes: [
        {
          id: 'map_requirements',
          type: 'Literal',
          config: { value: 'ok' },
          experience: {
            stage_id: 'understand',
            display_name: 'Map the call requirements',
            purpose: 'Identify what the final result must address.',
            contribution: 'Guides evidence collection.',
            expected_output: 'A checked requirement matrix',
            failure_message: 'This step could not finish; completed work remains safe.',
            visibility: 'standard',
          },
        },
      ],
      edges: [],
    };

    const result = roundTrip(workflow);
    expect(result.experience?.goal).toBe('Produce a checked requirement matrix.');
    expect(result.experience?.stages?.[0]).toEqual({
      id: 'understand',
      display_name: 'Understand the request',
      node_ids: ['map_requirements'],
    });
    expect(result.nodes[0].experience).toEqual({
      stage_id: 'understand',
      display_name: 'Map the call requirements',
      purpose: 'Identify what the final result must address.',
      contribution: 'Guides evidence collection.',
      expected_output: 'A checked requirement matrix',
      failure_message: 'This step could not finish; completed work remains safe.',
      visibility: 'standard',
    });
  });

  it('does not introduce an experience key for a legacy workflow that never had one', () => {
    const workflow: YamlWorkflow = {
      name: 'Legacy workflow',
      version: '1.0',
      nodes: [{ id: 'only', type: 'Literal', config: { value: 1 } }],
      edges: [],
    };

    const result = roundTrip(workflow);
    expect(result.experience).toBeUndefined();
    expect(result.nodes[0].experience).toBeUndefined();
  });
});
