import { describe, expect, it, vi } from 'vitest';
import type { Edge, Node } from 'reactflow';
import {
  applyStageCollapse,
  bandId,
  buildStageBandNodes,
  BUILDER_STAGE_BAND_TYPE,
  BUILDER_STAGE_PLACEHOLDER_TYPE,
  collapsibleStageIndexes,
  isSyntheticNodeId,
  placeholderId,
} from './stage-view';
import type { Stage } from '../flow-layout';
import type { WorkflowEdgeData, WorkflowNodeData } from '../yaml-bridge';

function node(
  id: string,
  x: number,
  y: number,
  data: Partial<WorkflowNodeData> = {},
): Node<WorkflowNodeData> {
  return {
    id,
    type: 'workflow',
    position: { x, y },
    width: 240,
    height: 92,
    data: { nodeId: id, typeName: 'AITaskAgent', config: {}, ...data },
  };
}

const nodes = [
  node('intake', 0, 0),
  node('approve', 320, 0, { experience: { display_name: 'Approve Order' } }),
  node('escalate', 320, 160, { hasIssue: true }),
  node('notify', 640, 0),
];
const edges: Edge<WorkflowEdgeData>[] = [
  { id: 'e1', source: 'intake', target: 'approve' },
  { id: 'e2', source: 'intake', target: 'escalate' },
  { id: 'e3', source: 'approve', target: 'notify' },
  { id: 'e4', source: 'escalate', target: 'notify' },
];
const stages: Stage[] = [
  { index: 0, label: 'Intake', xStart: 0, xEnd: 240, yStart: 0, yEnd: 92, nodeIds: ['intake'] },
  { index: 1, label: 'Review', xStart: 320, xEnd: 560, yStart: 0, yEnd: 252, nodeIds: ['approve', 'escalate'] },
  { index: 2, label: 'Notify', xStart: 640, xEnd: 880, yStart: 0, yEnd: 92, nodeIds: ['notify'] },
];

describe('buildStageBandNodes', () => {
  it('draws a band behind each expanded stage', () => {
    const bands = buildStageBandNodes(stages, new Set(), () => {});
    expect(bands).toHaveLength(3);
    expect(bands[0].type).toBe(BUILDER_STAGE_BAND_TYPE);
    expect(bands[0].zIndex).toBe(-1);
    expect(bands[0].draggable).toBe(false);
    // Wide enough to enclose the stage plus its padding, and tall enough to
    // leave room for the label above the nodes.
    expect(bands[1].width!).toBeGreaterThan(560 - 320);
    expect(bands[1].position.y).toBeLessThan(0);
    expect(bands[1].data.stepCount).toBe(2);
  });

  it('leaves a collapsed stage without a band, since its placeholder is one', () => {
    const bands = buildStageBandNodes(stages, new Set([1]), () => {});
    expect(bands.map(band => band.id)).toEqual([bandId(0), bandId(2)]);
  });

  it('reports which stage a collapse control belongs to', () => {
    const onCollapse = vi.fn();
    const [band] = buildStageBandNodes(stages, new Set(), onCollapse);
    band.data.onCollapse(band.data.stageIndex);
    expect(onCollapse).toHaveBeenCalledWith(0);
  });
});

describe('applyStageCollapse', () => {
  it('passes the graph through untouched when nothing is collapsed', () => {
    const result = applyStageCollapse(nodes, edges, stages, new Set(), () => {});
    expect(result.nodes).toBe(nodes);
    expect(result.edges).toBe(edges);
  });

  it('replaces a collapsed stage with one placeholder', () => {
    const { nodes: visible } = applyStageCollapse(nodes, edges, stages, new Set([1]), () => {});
    expect(visible.map(item => item.id)).toEqual(['intake', 'notify', placeholderId(1)]);
    const placeholder = visible.find(item => item.id === placeholderId(1))!;
    expect(placeholder.type).toBe(BUILDER_STAGE_PLACEHOLDER_TYPE);
    expect(placeholder.data).toMatchObject({
      label: 'Review',
      nodeIds: ['approve', 'escalate'],
      // Business names where they exist, node ids otherwise.
      stepLabels: ['Approve Order', 'escalate'],
      // A preflight problem must not be hidden by collapsing the stage.
      hasIssue: true,
    });
  });

  it('reroutes edges across the collapse and drops the ones inside it', () => {
    const collapsedNodes = [...nodes, node('review_sub', 320, 320)];
    const collapsedEdges: Edge<WorkflowEdgeData>[] = [
      ...edges,
      { id: 'e5', source: 'approve', target: 'review_sub' },
    ];
    const collapsedStages: Stage[] = [
      stages[0],
      { ...stages[1], nodeIds: ['approve', 'escalate', 'review_sub'] },
      stages[2],
    ];
    const { edges: rerouted } = applyStageCollapse(
      collapsedNodes,
      collapsedEdges,
      collapsedStages,
      new Set([1]),
      () => {},
    );
    // intake→(approve|escalate) collapses to one edge, as does (…)→notify, and
    // the edge wholly inside the stage disappears.
    expect(rerouted.map(edge => `${edge.source}->${edge.target}`)).toEqual([
      `intake->${placeholderId(1)}`,
      `${placeholderId(1)}->notify`,
    ]);
  });

  it('names its synthetic nodes so the Builder can keep them out of the YAML', () => {
    expect(isSyntheticNodeId(placeholderId(2))).toBe(true);
    expect(isSyntheticNodeId(bandId(0))).toBe(true);
    expect(isSyntheticNodeId('approve')).toBe(false);
  });
});

describe('collapsibleStageIndexes', () => {
  it('is the stages holding more than one step', () => {
    expect(collapsibleStageIndexes(stages)).toEqual([1]);
  });
});
