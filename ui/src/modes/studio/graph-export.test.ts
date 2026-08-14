import { describe, expect, it } from 'vitest';
import type { Edge, Node } from 'reactflow';
import {
  buildWorkflowSvg,
  edgePath,
  escapeXml,
  exportFileName,
  exportScale,
} from './graph-export';
import type { WorkflowEdgeData, WorkflowNodeData } from './yaml-bridge';

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
  node('intake', 0, 0, { typeName: 'WorkflowInputAgent', executionKind: 'input' }),
  node('classify', 320, 0, { executionKind: 'ai', selectedModel: 'auto' }),
  node('write_back', 640, 0, {
    typeName: 'MCPToolAgent',
    executionKind: 'external',
    mcpOperation: 'write',
    config: { server_id: 'dynamics_crm', tool: 'update_case' },
  }),
];
const edges: Edge<WorkflowEdgeData>[] = [
  { id: 'e1', source: 'intake', target: 'classify' },
  { id: 'e2', source: 'classify', target: 'write_back', data: { edgeKind: 'branch', groupId: 'g1', branchLabel: 'urgent' } },
];

describe('buildWorkflowSvg', () => {
  const image = buildWorkflowSvg({
    title: 'Customer Triage',
    subtitle: '3 steps · 2 connections',
    nodes,
    edges,
  });

  it('sizes the document around the whole graph, not the viewport', () => {
    // Rightmost node ends at 880; plus padding either side.
    expect(image.width).toBeGreaterThan(880);
    expect(image.height).toBeGreaterThan(92);
    expect(image.svg.startsWith('<svg')).toBe(true);
    expect(image.svg).toContain(`width="${image.width}"`);
  });

  it('draws every step with its canvas label and subtitle', () => {
    expect(image.svg).toContain('Customer Triage');
    expect(image.svg).toContain('>intake<');
    expect(image.svg).toContain('>classify<');
    // An MCP step names the system it reaches, exactly as the canvas does.
    expect(image.svg).toContain('MCP Tool · dynamics_crm');
  });

  it('carries the badges that make the automation boundary visible', () => {
    expect(image.svg).toContain('Uses model');
    expect(image.svg).toContain('External action');
    expect(image.svg).toContain('>write<');
    expect(image.svg).toContain('Best available model');
  });

  it('draws one connector per edge, with branch labels', () => {
    expect(image.svg.match(/marker-end="url\(#wf-arrow\)"/g)).toHaveLength(2);
    expect(image.svg).toContain('>urgent<');
  });

  it('prefers the business name when a step has one', () => {
    const withExperience = buildWorkflowSvg({
      title: 'W',
      subtitle: '',
      nodes: [node('classify', 0, 0, { experience: { display_name: 'Understand Request' } })],
      edges: [],
    });
    expect(withExperience.svg).toContain('Understand Request');
    // The technical id stays available as the subtitle.
    expect(withExperience.svg).toContain('AI Task · classify');
  });

  it('draws stage bands when the canvas is showing them', () => {
    const banded = buildWorkflowSvg({
      title: 'W',
      subtitle: '',
      nodes,
      edges,
      stages: [{ index: 0, label: 'Intake', xStart: 0, xEnd: 240, yStart: 0, yEnd: 92, nodeIds: ['intake'] }],
    });
    expect(banded.svg).toContain('INTAKE');
  });

  it('refuses to export an empty canvas', () => {
    expect(() => buildWorkflowSvg({ title: 'W', subtitle: '', nodes: [], edges: [] })).toThrow(/no steps/i);
  });

  it('escapes text that would otherwise break the document', () => {
    const hostile = buildWorkflowSvg({
      title: 'A & B <script>',
      subtitle: '"quoted"',
      nodes: [node('n1', 0, 0)],
      edges: [],
    });
    expect(hostile.svg).toContain('A &amp; B &lt;script&gt;');
    expect(hostile.svg).not.toContain('<script>');
  });
});

describe('escapeXml', () => {
  it('escapes the five XML entities', () => {
    expect(escapeXml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&apos;');
  });
});

describe('edgePath', () => {
  const box = (x: number, y: number) => ({ x, y, width: 240, height: 92 });

  it('runs straight across between steps on the same row', () => {
    expect(edgePath(box(0, 0), box(400, 0))).toBe('M 240 46 L 400 46');
  });

  it('steps through the midpoint when the rows differ', () => {
    const path = edgePath(box(0, 0), box(400, 200));
    expect(path.startsWith('M 240 46')).toBe(true);
    expect(path).toContain('Q');
    expect(path.endsWith('L 400 246')).toBe(true);
  });

  it('bows around a backwards edge instead of doubling over itself', () => {
    expect(edgePath(box(400, 0), box(0, 200))).toContain('C');
  });

  it('leaves the bottom edge and enters the top edge in a top-down layout', () => {
    // Same column: straight down from the source's bottom to the target's top.
    expect(edgePath(box(0, 0), box(0, 300), 'TB')).toBe('M 120 92 L 120 300');
    const across = edgePath(box(0, 0), box(400, 300), 'TB');
    expect(across.startsWith('M 120 92')).toBe(true);
    expect(across.endsWith('L 520 300')).toBe(true);
  });

  it('bows around an edge that runs back up the page', () => {
    expect(edgePath(box(0, 300), box(400, 0), 'TB')).toContain('C');
  });
});

describe('buildWorkflowSvg in a top-down layout', () => {
  it('routes every connector vertically', () => {
    const stacked = buildWorkflowSvg({
      title: 'W',
      subtitle: '',
      direction: 'TB',
      nodes: [node('a', 0, 0), node('b', 0, 300)],
      edges: [{ id: 'e1', source: 'a', target: 'b' }],
    });
    // Down the middle of the two cards, not out of their right-hand edges.
    expect(stacked.svg).toContain('d="M 120 92 L 120 300"');
  });
});

describe('exportScale', () => {
  it('honours the requested scale for an ordinary graph', () => {
    expect(exportScale(1200, 800, 3)).toBe(3);
  });

  it('pulls back so a huge graph still fits in a canvas', () => {
    const wide = exportScale(9000, 1200, 3);
    expect(wide).toBeLessThan(3);
    expect(9000 * wide).toBeLessThanOrEqual(12000);

    const large = exportScale(6000, 5000, 3);
    expect(6000 * large * 5000 * large).toBeLessThanOrEqual(40_000_000);
  });
});

describe('exportFileName', () => {
  it('uses the saved workflow slug when there is one', () => {
    expect(exportFileName('pump_case_routing', 'Pump Case Routing', 'png')).toBe('pump-case-routing.png');
  });

  it('falls back to the display title for an unsaved draft', () => {
    expect(exportFileName(null, 'Customer Triage (v2)', 'svg')).toBe('customer-triage-v2.svg');
    expect(exportFileName(null, '···', 'png')).toBe('workflow.png');
  });
});
