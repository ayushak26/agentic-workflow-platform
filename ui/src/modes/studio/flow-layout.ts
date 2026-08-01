import * as dagre from 'dagre';
import type { Edge, Node } from 'reactflow';

const NODE_WIDTH = 260;
const NODE_HEIGHT = 92;
// How close two nodes' x-positions need to be (in LR layout) to count as
// the same dagre column/rank for stage-banding purposes. Dagre snaps nodes
// in the same rank to the same x, but floating point + differing widths
// mean "same column" is safer read as "within half a node width" than as
// exact equality.
const STAGE_TOLERANCE = NODE_WIDTH / 2;

export type Stage = {
  index: number;
  label: string;
  xStart: number;
  xEnd: number;
  yStart: number;
  yEnd: number;
  nodeIds: string[];
};

export type LayoutResult<T> = {
  nodes: Node<T>[];
  stages: Stage[];
};

/**
 * Lays out nodes with dagre and, in LR mode, groups them into "stages" by
 * observed x-column so the graph can render a background band + label per
 * stage and collapse a stage's parallel nodes into one placeholder. Grouped
 * by observed position rather than dagre's internal rank field, which is
 * both undocumented in the public API and unnecessary here — the x
 * coordinates it already produced are enough.
 *
 * Only ever called on a structural change to the workflow graph (a new
 * parsed YAML) — never on a node-status tick — so node positions stay
 * fixed for the life of a run.
 */
export function layoutFlow<T>(
  nodes: Node<T>[],
  edges: Edge[],
  direction: 'TB' | 'LR' = 'LR',
): LayoutResult<T> {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: direction,
    ranksep: 96,
    nodesep: 56,
    marginx: 40,
    marginy: 40,
  });

  for (const node of nodes) {
    graph.setNode(node.id, {
      width: node.width ?? NODE_WIDTH,
      height: node.height ?? NODE_HEIGHT,
    });
  }
  for (const edge of edges) {
    graph.setEdge(edge.source, edge.target);
  }

  dagre.layout(graph);

  const laidOut = nodes.map((node) => {
    const point = graph.node(node.id);
    const width = node.width ?? NODE_WIDTH;
    const height = node.height ?? NODE_HEIGHT;
    return {
      ...node,
      position: {
        x: point.x - width / 2,
        y: point.y - height / 2,
      },
    };
  });

  return { nodes: laidOut, stages: groupIntoStages(laidOut, direction) };
}

// A stage's label prefers the node ids' shared leading-letters prefix
// (e.g. "rag_1_1"/"rag_1_2"/"rag_2_1" → "Rag") over a generic "Stage N" —
// production workflows (see workflows/horizon_v4.yaml) name parallel
// branches this way, so this reads as a real section name more often than
// not, falling back to "Stage N" when the ids don't agree.
function stageLabel(nodeIds: string[], index: number): string {
  const prefixes = nodeIds.map((id) => (id.match(/^[a-zA-Z]+/)?.[0] ?? '').toLowerCase());
  const counts = new Map<string, number>();
  for (const prefix of prefixes) {
    if (!prefix) continue;
    counts.set(prefix, (counts.get(prefix) ?? 0) + 1);
  }
  let best = '';
  let bestCount = 0;
  for (const [prefix, count] of counts) {
    if (count > bestCount) { best = prefix; bestCount = count; }
  }
  if (best && bestCount / nodeIds.length >= 0.5) {
    return best.charAt(0).toUpperCase() + best.slice(1);
  }
  return `Stage ${index + 1}`;
}

function groupIntoStages<T>(nodes: Node<T>[], direction: 'TB' | 'LR'): Stage[] {
  // In LR mode, "stage" = column (x-position); in TB mode it'd be row
  // (y-position) — stage bands are drawn along whichever axis execution
  // flows across.
  const axis = direction === 'LR' ? 'x' : 'y';
  const columns: { key: number; nodes: Node<T>[] }[] = [];
  for (const node of nodes) {
    const value = node.position[axis];
    let column = columns.find((c) => Math.abs(c.key - value) <= STAGE_TOLERANCE);
    if (!column) {
      column = { key: value, nodes: [] };
      columns.push(column);
    }
    column.nodes.push(node);
  }
  columns.sort((a, b) => a.key - b.key);
  return columns.map((column, index) => {
    const xs = column.nodes.map((n) => n.position.x);
    const ys = column.nodes.map((n) => n.position.y);
    const widths = column.nodes.map((n) => n.width ?? NODE_WIDTH);
    const heights = column.nodes.map((n) => n.height ?? NODE_HEIGHT);
    const nodeIds = column.nodes.map((n) => n.id);
    return {
      index,
      label: stageLabel(nodeIds, index),
      xStart: Math.min(...xs),
      xEnd: Math.max(...xs.map((x, i) => x + widths[i])),
      yStart: Math.min(...ys),
      yEnd: Math.max(...ys.map((y, i) => y + heights[i])),
      nodeIds,
    };
  });
}
