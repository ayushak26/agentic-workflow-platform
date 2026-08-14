import { describe, expect, it } from 'vitest';
import type { Edge, Node } from 'reactflow';
import { firstNodeId, resolveArrowTarget } from './graph-navigation';
import type { WorkflowNodeData } from './yaml-bridge';

function node(id: string, x: number, y: number): Node<WorkflowNodeData> {
  return {
    id,
    type: 'workflow',
    position: { x, y },
    width: 240,
    height: 92,
    data: { nodeId: id, typeName: 'AITaskAgent', config: {} },
  };
}

function edge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target };
}

/*
 *   intake ──► classify ──┬─► approve ──► notify
 *                         └─► escalate ─┘
 *
 * A fan-out is the case that makes pure geometry feel random, so it is the
 * shape most of these assertions are about.
 */
const nodes = [
  node('intake', 0, 100),
  node('classify', 320, 100),
  node('approve', 640, 0),
  node('escalate', 640, 200),
  node('notify', 960, 100),
];
const edges = [
  edge('intake', 'classify'),
  edge('classify', 'approve'),
  edge('classify', 'escalate'),
  edge('approve', 'notify'),
  edge('escalate', 'notify'),
];

describe('resolveArrowTarget', () => {
  it('follows a connection rather than the nearest box', () => {
    expect(resolveArrowTarget(nodes, edges, 'intake', 'right')).toBe('classify');
    expect(resolveArrowTarget(nodes, edges, 'notify', 'left')).toBe('approve');
  });

  it('takes the branch closest in line with the current step on a fan-out', () => {
    // Both branches are one edge away; 'approve' is nearer classify's own row.
    expect(resolveArrowTarget(nodes, edges, 'classify', 'right')).toBe('approve');
  });

  it('moves between parallel branches with up and down', () => {
    expect(resolveArrowTarget(nodes, edges, 'approve', 'down')).toBe('escalate');
    expect(resolveArrowTarget(nodes, edges, 'escalate', 'up')).toBe('approve');
  });

  it('prefers a step in the same column over a closer one in another column', () => {
    const withNearbyOtherColumn = [...nodes, node('aside', 320, 260)];
    // 'aside' is vertically closer to escalate than approve is, but sits in the
    // classify column — moving up from escalate should stay in its own stage.
    expect(resolveArrowTarget(withNearbyOtherColumn, edges, 'escalate', 'up')).toBe('approve');
  });

  it('falls back to geometry for a step that is not wired up yet', () => {
    const orphaned = [...nodes, node('draft', 1280, 100)];
    expect(resolveArrowTarget(orphaned, edges, 'notify', 'right')).toBe('draft');
  });

  it('stays put at the end of the graph', () => {
    expect(resolveArrowTarget(nodes, edges, 'notify', 'right')).toBeNull();
    expect(resolveArrowTarget(nodes, edges, 'intake', 'left')).toBeNull();
    expect(resolveArrowTarget([], [], null, 'right')).toBeNull();
  });

  it('enters the graph at its first step when nothing is selected', () => {
    expect(resolveArrowTarget(nodes, edges, null, 'right')).toBe('intake');
    // A selection pointing at a step that no longer exists behaves the same way.
    expect(resolveArrowTarget(nodes, edges, 'deleted', 'down')).toBe('intake');
  });
});

describe('firstNodeId', () => {
  it('is the leftmost step, breaking ties on the topmost', () => {
    expect(firstNodeId([node('b', 100, 50), node('a', 0, 80), node('c', 0, 10)])).toBe('c');
    expect(firstNodeId([])).toBeNull();
  });
});
