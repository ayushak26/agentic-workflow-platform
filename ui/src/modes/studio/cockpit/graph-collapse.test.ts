/* eslint-disable @typescript-eslint/no-explicit-any -- test fixtures mirror the loosely-typed graph node data */
import { describe, expect, it } from 'vitest';
import type { Node } from 'reactflow';
import { applyStageCollapse, STAGE_PLACEHOLDER_TYPE } from './graph-collapse';
import type { Stage } from '../flow-layout';

function node(id: string, x: number, status = 'pending'): Node<any> {
  return {
    id,
    type: 'workflow',
    position: { x, y: 0 },
    width: 240,
    height: 92,
    data: { nodeId: id, typeName: 'TestNode', status },
  };
}

describe('applyStageCollapse', () => {
  const nodes = [node('a', 0), node('b', 300, 'active'), node('c', 300, 'done'), node('d', 600)];
  const edges = [
    { id: 'e1', source: 'a', target: 'b' },
    { id: 'e2', source: 'a', target: 'c' },
    { id: 'e3', source: 'b', target: 'd' },
    { id: 'e4', source: 'c', target: 'd' },
  ];
  const stages: Stage[] = [
    { index: 0, label: 'Stage 1', xStart: 0, xEnd: 240, yStart: 0, yEnd: 92, nodeIds: ['a'] },
    { index: 1, label: 'Stage 2', xStart: 300, xEnd: 540, yStart: 0, yEnd: 192, nodeIds: ['b', 'c'] },
    { index: 2, label: 'Stage 3', xStart: 600, xEnd: 840, yStart: 0, yEnd: 92, nodeIds: ['d'] },
  ];

  it('passes everything through unchanged when nothing is collapsed', () => {
    const result = applyStageCollapse(nodes, edges, stages, new Set());
    expect(result.nodes).toBe(nodes);
    expect(result.edges).toBe(edges);
  });

  it('replaces a collapsed stage\'s members with one placeholder and reroutes edges', () => {
    const result = applyStageCollapse(nodes, edges, stages, new Set([1]));
    const ids = result.nodes.map((n) => n.id);
    expect(ids).not.toContain('b');
    expect(ids).not.toContain('c');
    const placeholder = result.nodes.find((n) => n.type === STAGE_PLACEHOLDER_TYPE);
    expect(placeholder).toBeDefined();
    expect(placeholder!.data.nodeIds).toEqual(['b', 'c']);
    expect(placeholder!.data.counts.running).toBe(1);
    expect(placeholder!.data.counts.completed).toBe(1);

    // a -> placeholder (deduped from two edges), placeholder -> d (deduped)
    expect(result.edges).toHaveLength(2);
    const sources = result.edges.map((e) => `${e.source}->${e.target}`);
    expect(sources).toContain('a->__stage_placeholder_1__');
    expect(sources).toContain('__stage_placeholder_1__->d');
  });
});
