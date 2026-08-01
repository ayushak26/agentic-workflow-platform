/* Node data shapes vary by node type (regular node vs. collapsed-stage placeholder). */
/* eslint-disable @typescript-eslint/no-explicit-any */
import type { Edge, Node } from 'reactflow';
import type { Stage } from '../flow-layout';
import { computeStatusCounts, type NodeStatus } from '../cockpit-state';
import { STAGE_BAND_TYPE, type StageBandData } from './StageBandNode';

export const STAGE_PLACEHOLDER_TYPE = 'stagePlaceholder';

const BAND_PADDING = 24;
const BAND_LABEL_HEADROOM = 28;

/**
 * Background band nodes for every EXPANDED stage (a collapsed stage is
 * already fully represented by its one placeholder node, so it gets no
 * band). Rendered with a negative zIndex so they sit behind the real
 * nodes regardless of array order.
 */
export function buildStageBandNodes(
  stages: Stage[],
  collapsedStageIndexes: ReadonlySet<number>,
): Node<StageBandData>[] {
  return stages
    .filter((stage) => !collapsedStageIndexes.has(stage.index))
    .map((stage) => {
      const width = stage.xEnd - stage.xStart + BAND_PADDING * 2;
      const height = stage.yEnd - stage.yStart + BAND_PADDING * 2 + BAND_LABEL_HEADROOM;
      return {
        id: `__stage_band_${stage.index}__`,
        type: STAGE_BAND_TYPE,
        position: { x: stage.xStart - BAND_PADDING, y: stage.yStart - BAND_PADDING - BAND_LABEL_HEADROOM },
        width,
        height,
        data: { label: stage.label, width, height },
        selectable: false,
        draggable: false,
        zIndex: -1,
      };
    });
}

export type StagePlaceholderData = {
  stageIndex: number;
  label: string;
  nodeIds: string[];
  counts: ReturnType<typeof computeStatusCounts>;
};

function placeholderId(stageIndex: number): string {
  return `__stage_placeholder_${stageIndex}__`;
}

/**
 * Replaces a collapsed stage's member nodes with a single placeholder node
 * (positioned over that stage's bounding box) and reroutes any edge that
 * touched a hidden member to/from the placeholder instead, so overall
 * connectivity stays visible. Nodes/edges for stages that aren't collapsed
 * pass through untouched. Pure and cheap enough to recompute on every
 * render — it never re-runs dagre.
 */
export function applyStageCollapse(
  nodes: Node<any>[],
  edges: Edge[],
  stages: Stage[],
  collapsedStageIndexes: ReadonlySet<number>,
): { nodes: Node<any>[]; edges: Edge[] } {
  if (collapsedStageIndexes.size === 0) return { nodes, edges };

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const stageForNode = new Map<string, number>();
  for (const stage of stages) {
    for (const id of stage.nodeIds) stageForNode.set(id, stage.index);
  }

  const collapsedStages = stages.filter((s) => collapsedStageIndexes.has(s.index));
  const hiddenIds = new Set(collapsedStages.flatMap((s) => s.nodeIds));

  const placeholders: Node<any>[] = collapsedStages.map((stage) => {
    const statuses: Record<string, NodeStatus> = {};
    for (const id of stage.nodeIds) {
      const status = nodeById.get(id)?.data?.status;
      if (status) statuses[id] = status;
    }
    const width = 220;
    const height = 72;
    const data: StagePlaceholderData = {
      stageIndex: stage.index,
      label: stage.label,
      nodeIds: stage.nodeIds,
      counts: computeStatusCounts(statuses),
    };
    return {
      id: placeholderId(stage.index),
      type: STAGE_PLACEHOLDER_TYPE,
      position: {
        x: (stage.xStart + stage.xEnd) / 2 - width / 2,
        y: (stage.yStart + stage.yEnd) / 2 - height / 2,
      },
      width,
      height,
      data,
      selectable: false,
      draggable: false,
    };
  });

  const visibleNodes = nodes.filter((n) => !hiddenIds.has(n.id));

  function resolveEndpoint(id: string): string {
    if (!hiddenIds.has(id)) return id;
    const stageIndex = stageForNode.get(id);
    return stageIndex != null ? placeholderId(stageIndex) : id;
  }

  const seenEdgeKeys = new Set<string>();
  const remappedEdges: Edge[] = [];
  for (const edge of edges) {
    const source = resolveEndpoint(edge.source);
    const target = resolveEndpoint(edge.target);
    if (source === target) continue; // both ends collapsed into the same stage — drop
    const key = `${source}->${target}`;
    if (seenEdgeKeys.has(key)) continue;
    seenEdgeKeys.add(key);
    remappedEdges.push({ ...edge, id: `collapsed:${key}`, source, target, label: undefined });
  }

  return { nodes: [...visibleNodes, ...placeholders], edges: remappedEdges };
}
