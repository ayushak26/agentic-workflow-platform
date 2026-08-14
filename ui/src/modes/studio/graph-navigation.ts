import type { Edge, Node } from 'reactflow';

/**
 * Keyboard travel across a workflow graph.
 *
 * On a long workflow, dragging the canvas to find "the step after this one" is
 * the slowest thing in the Builder. Arrow keys make the graph walkable: left
 * and right follow the actual connections (which is what "next step" means in
 * a workflow — not "the nearest box to the right"), and up/down move between
 * parallel branches sitting in the same column.
 *
 * Geometry is only the fallback, used when the graph offers no edge to follow —
 * a step that is not wired up yet is still reachable, otherwise disconnected
 * nodes would be invisible to the keyboard.
 */

export type NavDirection = 'left' | 'right' | 'up' | 'down';

export const ARROW_DIRECTIONS: Record<string, NavDirection> = {
  ArrowLeft: 'left',
  ArrowRight: 'right',
  ArrowUp: 'up',
  ArrowDown: 'down',
};

export const DEFAULT_NODE_WIDTH = 260;
export const DEFAULT_NODE_HEIGHT = 92;

// Two nodes count as "the same column" (so up/down treats them as parallel
// branches of one stage) when their centres are within this distance on x.
// Dagre puts a rank's nodes at the same x, but manual dragging and differing
// node widths mean exact equality is too strict.
const COLUMN_TOLERANCE = DEFAULT_NODE_WIDTH * 0.75;
// Off-axis drift is penalised rather than forbidden when falling back to
// geometry, so "right" prefers a node straight ahead over one far off to the side.
const OFF_AXIS_PENALTY = 1.6;

export type NodeCentre = { id: string; x: number; y: number };

export function nodeCentre<T>(node: Node<T>): NodeCentre {
  return {
    id: node.id,
    x: node.position.x + (node.width ?? DEFAULT_NODE_WIDTH) / 2,
    y: node.position.y + (node.height ?? DEFAULT_NODE_HEIGHT) / 2,
  };
}

/** Leftmost, then topmost — where keyboard navigation starts with nothing selected. */
export function firstNodeId<T>(nodes: Node<T>[]): string | null {
  const centres = nodes.map(nodeCentre);
  if (centres.length === 0) return null;
  return centres.reduce((best, candidate) => (
    candidate.x < best.x || (candidate.x === best.x && candidate.y < best.y) ? candidate : best
  )).id;
}

function connected(edges: Edge[], nodeId: string, direction: 'left' | 'right'): Set<string> {
  const ids = new Set<string>();
  for (const edge of edges) {
    if (direction === 'right' && edge.source === nodeId) ids.add(edge.target);
    if (direction === 'left' && edge.target === nodeId) ids.add(edge.source);
  }
  return ids;
}

/**
 * The node an arrow key should move the selection to, or null when there is
 * nowhere to go (so the caller can leave the selection — and the viewport —
 * exactly where it is rather than jumping somewhere arbitrary).
 */
export function resolveArrowTarget<T>(
  nodes: Node<T>[],
  edges: Edge[],
  currentId: string | null,
  direction: NavDirection,
): string | null {
  if (nodes.length === 0) return null;
  if (!currentId || !nodes.some(node => node.id === currentId)) return firstNodeId(nodes);

  const centres = new Map(nodes.map(node => [node.id, nodeCentre(node)]));
  const current = centres.get(currentId)!;

  if (direction === 'left' || direction === 'right') {
    // Follow the workflow's own wiring first. Among several branches out of one
    // step, the closest in y is the one the reader's eye is already on.
    const neighbours = [...connected(edges, currentId, direction)]
      .map(id => centres.get(id))
      .filter((centre): centre is NodeCentre => Boolean(centre));
    if (neighbours.length > 0) {
      return neighbours.reduce((best, candidate) => (
        Math.abs(candidate.y - current.y) < Math.abs(best.y - current.y) ? candidate : best
      )).id;
    }
  }

  const forward = ({ x, y }: NodeCentre) => {
    switch (direction) {
      case 'right': return x - current.x;
      case 'left': return current.x - x;
      case 'down': return y - current.y;
      case 'up': return current.y - y;
    }
  };
  const offAxis = ({ x, y }: NodeCentre) => (
    direction === 'left' || direction === 'right'
      ? Math.abs(y - current.y)
      : Math.abs(x - current.x)
  );

  const ahead = [...centres.values()]
    .filter(centre => centre.id !== currentId && forward(centre) > 1);
  if (ahead.length === 0) return null;

  // Up/down inside one column means "the next parallel branch of this stage",
  // which must win over a closer node in a neighbouring column.
  if (direction === 'up' || direction === 'down') {
    const sameColumn = ahead.filter(centre => offAxis(centre) <= COLUMN_TOLERANCE);
    if (sameColumn.length > 0) {
      return sameColumn.reduce((best, candidate) => (
        forward(candidate) < forward(best) ? candidate : best
      )).id;
    }
  }

  const score = (centre: NodeCentre) => forward(centre) + OFF_AXIS_PENALTY * offAxis(centre);
  return ahead.reduce((best, candidate) => (
    score(candidate) < score(best) ? candidate : best
  )).id;
}
