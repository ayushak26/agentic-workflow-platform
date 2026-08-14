import type { Edge, Node } from 'reactflow';
import type { Stage } from './flow-layout';
import { nodeTypeLabel } from './node-presentation';
import type { WorkflowEdgeData, WorkflowNodeData } from './yaml-bridge';

/**
 * Renders a workflow graph to a standalone SVG document, and rasterises that
 * to PNG.
 *
 * Deliberately NOT a screenshot of the canvas. A screenshot captures the
 * visible viewport, which is exactly what fails for the workflows people
 * actually need to hand around — a 40-step graph is never fully on screen, and
 * the parts that are on screen are at whatever zoom the author happened to
 * leave behind. This draws every node from its layout coordinates at a fixed,
 * legible scale, so the image is complete and reads the same whatever the
 * canvas was doing.
 *
 * Self-contained by construction: no external CSS, no web fonts, no <img>
 * references. That is what lets the browser rasterise it through a data: URL
 * without tainting the canvas, and what makes the SVG usable in a document
 * that knows nothing about this app.
 */

const DEFAULT_NODE_WIDTH = 260;
const DEFAULT_NODE_HEIGHT = 92;
const MIN_CARD_HEIGHT = 88;
const PADDING = 48;
const HEADER_HEIGHT = 74;
const BAND_LABEL_HEADROOM = 26;

// Spelled as hex rather than the app's CSS custom properties: an exported file
// has no stylesheet to resolve var() against. Values mirror the Tailwind
// palette the canvas uses, and the execution-kind labels mirror
// builder/ExecutionKindBadge's EXECUTION_KINDS.
const COLORS = {
  page: '#ffffff',
  title: '#0b1c2c',
  meta: '#64748b',
  rule: '#e2e8f0',
  card: '#ffffff',
  cardBorder: '#cbd5e1',
  cardBorderIssue: '#f87171',
  cardText: '#0f172a',
  cardMeta: '#64748b',
  edge: '#94a3b8',
  band: '#f8fafc',
  bandBorder: '#e2e8f0',
  bandLabel: '#94a3b8',
} as const;

type BadgeStyle = { label: string; fill: string; text: string; border: string };

const KIND_BADGES: Record<string, BadgeStyle> = {
  ai: { label: 'Uses model', fill: '#f5f3ff', text: '#6d28d9', border: '#ddd6fe' },
  deterministic: { label: 'Deterministic', fill: '#ecfdf5', text: '#047857', border: '#a7f3d0' },
  external: { label: 'External action', fill: '#fffbeb', text: '#92400e', border: '#fde68a' },
  human: { label: 'Human decision', fill: '#f0f9ff', text: '#0369a1', border: '#bae6fd' },
  input: { label: 'Input', fill: '#f1f5f9', text: '#334155', border: '#e2e8f0' },
  output: { label: 'Output', fill: '#f1f5f9', text: '#334155', border: '#e2e8f0' },
};

const NEUTRAL_BADGE: BadgeStyle = { label: '', fill: '#f1f5f9', text: '#475569', border: '#e2e8f0' };
const READ_BADGE: BadgeStyle = { label: 'read', fill: '#ecfdf5', text: '#047857', border: '#a7f3d0' };
const WRITE_BADGE: BadgeStyle = { label: 'write', fill: '#fffbeb', text: '#92400e', border: '#fde68a' };

export function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

// Text is laid out without a measuring context (the SVG is built as a string,
// possibly before it is ever attached to a document), so widths are estimated
// from the font size. Erring narrow is the safe direction: a slightly early
// ellipsis beats text spilling out of its card.
function truncate(text: string, available: number, fontSize: number): string {
  const charWidth = fontSize * 0.56;
  const maxChars = Math.max(3, Math.floor(available / charWidth));
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars - 1).trimEnd()}…`;
}

function badgeWidth(label: string): number {
  return Math.round(label.length * 5.1 + 16);
}

function nodeBadges(data: WorkflowNodeData): BadgeStyle[] {
  const badges: BadgeStyle[] = [];
  const kind = data.executionKind ? KIND_BADGES[data.executionKind] : undefined;
  if (kind) badges.push(kind);
  if (data.mcpOperation === 'read') badges.push(READ_BADGE);
  if (data.mcpOperation === 'write') badges.push(WRITE_BADGE);
  const requestedModel = data.selectedModel ?? data.config.model;
  if (typeof requestedModel === 'string' && requestedModel) {
    badges.push({
      ...NEUTRAL_BADGE,
      label: requestedModel === 'auto' ? 'Best available model' : requestedModel,
    });
  }
  return badges;
}

type Box = { x: number; y: number; width: number; height: number };

function boxOf(node: Node<unknown>): Box {
  return {
    x: node.position.x,
    y: node.position.y,
    width: node.width ?? DEFAULT_NODE_WIDTH,
    height: Math.max(node.height ?? DEFAULT_NODE_HEIGHT, MIN_CARD_HEIGHT),
  };
}

export type FlowDirection = 'LR' | 'TB';

/**
 * A smoothstep-shaped connector matching the canvas: out of the source's
 * trailing edge, across at the midpoint, into the target's leading edge, with
 * rounded corners. Which edges those are follows the layout direction — the
 * handles move with it on the canvas, and a diagram whose arrows leave from the
 * wrong side of every box is unreadable regardless of how neat the boxes are.
 *
 * Edges running backwards (a loop-back, or a step dragged upstream of its
 * source) cannot be drawn that way without crossing their own endpoints, so
 * those get a cubic bow instead.
 */
export function edgePath(source: Box, target: Box, direction: FlowDirection = 'LR'): string {
  // Solved once along the axis of flow, then mirrored for top-down: `a` is the
  // direction of travel, `b` the axis the connector steps across.
  const vertical = direction === 'TB';
  const sa = vertical ? source.y + source.height : source.x + source.width;
  const sb = vertical ? source.x + source.width / 2 : source.y + source.height / 2;
  const ta = vertical ? target.y : target.x;
  const tb = vertical ? target.x + target.width / 2 : target.y + target.height / 2;
  const point = (a: number, b: number) => (vertical ? `${b} ${a}` : `${a} ${b}`);

  if (ta <= sa + 32) {
    const bow = Math.max(64, Math.abs(ta - sa) / 2);
    return `M ${point(sa, sb)} C ${point(sa + bow, sb)}, ${point(ta - bow, tb)}, ${point(ta, tb)}`;
  }
  if (Math.abs(tb - sb) < 2) return `M ${point(sa, sb)} L ${point(ta, tb)}`;

  const mid = (sa + ta) / 2;
  const away = tb > sb ? 1 : -1;
  const radius = Math.min(14, Math.abs(tb - sb) / 2, (ta - sa) / 2);
  return [
    `M ${point(sa, sb)}`,
    `L ${point(mid - radius, sb)}`,
    `Q ${point(mid, sb)} ${point(mid, sb + radius * away)}`,
    `L ${point(mid, tb - radius * away)}`,
    `Q ${point(mid, tb)} ${point(mid + radius, tb)}`,
    `L ${point(ta, tb)}`,
  ].join(' ');
}

export type WorkflowSvgOptions = {
  title: string;
  subtitle: string;
  nodes: Node<WorkflowNodeData>[];
  edges: Edge<WorkflowEdgeData>[];
  /** Drawn as background bands when the canvas is showing stages. */
  stages?: Stage[];
  /** Which way the graph flows, so connectors leave and enter the right edges. */
  direction?: FlowDirection;
};

export type WorkflowSvg = { svg: string; width: number; height: number };

export function buildWorkflowSvg({
  title,
  subtitle,
  nodes,
  edges,
  stages = [],
  direction = 'LR',
}: WorkflowSvgOptions): WorkflowSvg {
  if (nodes.length === 0) throw new Error('There are no steps to export yet.');

  const boxes = new Map(nodes.map(node => [node.id, boxOf(node)]));
  const values = [...boxes.values()];
  const bandTop = stages.length > 0 ? BAND_LABEL_HEADROOM + 12 : 0;
  const minX = Math.min(...values.map(box => box.x)) - (stages.length > 0 ? 24 : 0);
  const minY = Math.min(...values.map(box => box.y)) - bandTop;
  const maxX = Math.max(...values.map(box => box.x + box.width)) + (stages.length > 0 ? 24 : 0);
  const maxY = Math.max(...values.map(box => box.y + box.height)) + (stages.length > 0 ? 16 : 0);

  const width = Math.ceil(maxX - minX + PADDING * 2);
  const height = Math.ceil(maxY - minY + PADDING * 2 + HEADER_HEIGHT);
  const offsetX = PADDING - minX;
  const offsetY = PADDING + HEADER_HEIGHT - minY;

  const bandMarkup = stages.map(stage => {
    const bandX = stage.xStart - 24;
    const bandY = stage.yStart - BAND_LABEL_HEADROOM - 12;
    const bandWidth = stage.xEnd - stage.xStart + 48;
    const bandHeight = stage.yEnd - stage.yStart + BAND_LABEL_HEADROOM + 28;
    return [
      `<rect x="${bandX}" y="${bandY}" width="${bandWidth}" height="${bandHeight}" rx="10" fill="${COLORS.band}" stroke="${COLORS.bandBorder}" />`,
      `<text x="${bandX + 12}" y="${bandY + 17}" font-size="10" letter-spacing="0.06em" fill="${COLORS.bandLabel}">${escapeXml(stage.label.toUpperCase())}</text>`,
    ].join('');
  }).join('');

  const edgeMarkup = edges.map(edge => {
    const source = boxes.get(edge.source);
    const target = boxes.get(edge.target);
    if (!source || !target) return '';
    const path = `<path d="${edgePath(source, target, direction)}" fill="none" stroke="${COLORS.edge}" stroke-width="1.6" marker-end="url(#wf-arrow)" />`;
    const label = edge.data?.branchLabel ?? (typeof edge.label === 'string' ? edge.label : '');
    if (!label) return path;
    const labelX = direction === 'TB'
      ? (source.x + target.x + source.width / 2 + target.width / 2) / 2
      : (source.x + source.width + target.x) / 2;
    const labelY = direction === 'TB'
      ? (source.y + source.height + target.y) / 2
      : (source.y + source.height / 2 + target.y + target.height / 2) / 2;
    const labelWidth = badgeWidth(label);
    return [
      path,
      `<rect x="${labelX - labelWidth / 2}" y="${labelY - 9}" width="${labelWidth}" height="18" rx="9" fill="${COLORS.page}" stroke="${COLORS.rule}" />`,
      `<text x="${labelX}" y="${labelY + 4}" font-size="10" font-weight="600" text-anchor="middle" fill="${COLORS.cardMeta}">${escapeXml(label)}</text>`,
    ].join('');
  }).join('');

  const nodeMarkup = nodes.map(node => {
    const box = boxes.get(node.id)!;
    const businessLabel = node.data.experience?.display_name?.trim();
    const heading = businessLabel || node.data.nodeId;
    const subtitleText = businessLabel
      ? `${nodeTypeLabel(node.data.typeName, node.data.config)} · ${node.data.nodeId}`
      : nodeTypeLabel(node.data.typeName, node.data.config);
    const inner = box.width - 28;

    let badgeX = box.x + 14;
    const badgeY = box.y + box.height - 30;
    const badgeMarkup = nodeBadges(node.data).map(badge => {
      const pillWidth = badgeWidth(badge.label);
      if (badgeX + pillWidth > box.x + box.width - 10) return '';
      const markup = [
        `<rect x="${badgeX}" y="${badgeY}" width="${pillWidth}" height="17" rx="8.5" fill="${badge.fill}" stroke="${badge.border}" />`,
        `<text x="${badgeX + pillWidth / 2}" y="${badgeY + 12}" font-size="9" font-weight="600" text-anchor="middle" fill="${badge.text}">${escapeXml(badge.label)}</text>`,
      ].join('');
      badgeX += pillWidth + 5;
      return markup;
    }).join('');

    return [
      `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="10" fill="${COLORS.card}" stroke="${node.data.hasIssue ? COLORS.cardBorderIssue : COLORS.cardBorder}" stroke-width="1.6" />`,
      `<text x="${box.x + 14}" y="${box.y + 26}" font-size="13.5" font-weight="600" fill="${COLORS.cardText}">${escapeXml(truncate(heading, inner, 13.5))}</text>`,
      `<text x="${box.x + 14}" y="${box.y + 43}" font-size="10.5" fill="${COLORS.cardMeta}">${escapeXml(truncate(subtitleText, inner, 10.5))}</text>`,
      badgeMarkup,
    ].join('');
  }).join('');

  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="Inter, ui-sans-serif, system-ui, -apple-system, &quot;Segoe UI&quot;, sans-serif">`,
    `<defs><marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="${COLORS.edge}" /></marker></defs>`,
    `<rect width="${width}" height="${height}" fill="${COLORS.page}" />`,
    `<text x="${PADDING}" y="${PADDING - 8}" font-size="19" font-weight="600" fill="${COLORS.title}">${escapeXml(truncate(title, width - PADDING * 2, 19))}</text>`,
    `<text x="${PADDING}" y="${PADDING + 14}" font-size="11.5" fill="${COLORS.meta}">${escapeXml(truncate(subtitle, width - PADDING * 2, 11.5))}</text>`,
    `<line x1="${PADDING}" y1="${PADDING + 30}" x2="${width - PADDING}" y2="${PADDING + 30}" stroke="${COLORS.rule}" />`,
    `<g transform="translate(${offsetX} ${offsetY})">${bandMarkup}${edgeMarkup}${nodeMarkup}</g>`,
    '</svg>',
  ].join('');

  return { svg, width, height };
}

// Browsers reject canvases past an implementation-defined size (Safari is the
// strictest), and a silently blank PNG is worse than a slightly smaller one, so
// the requested scale is reduced until both the longest edge and the total
// pixel count are safely inside every engine's limits.
const MAX_CANVAS_DIMENSION = 12000;
const MAX_CANVAS_PIXELS = 40_000_000;

export function exportScale(width: number, height: number, desired = 2): number {
  return Math.min(
    Math.max(desired, 0.25),
    MAX_CANVAS_DIMENSION / width,
    MAX_CANVAS_DIMENSION / height,
    Math.sqrt(MAX_CANVAS_PIXELS / (width * height)),
  );
}

export function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/**
 * 3× by default: the export is meant to survive being dropped into a slide and
 * then zoomed into, and at 1× a 10px node subtitle is unreadable on any display
 * that isn't the one it was rendered on. exportScale() pulls this back when the
 * graph is large enough that 3× would exceed what a canvas can hold.
 */
export const DEFAULT_PNG_SCALE = 3;

export async function svgToPngBlob(
  { svg, width, height }: WorkflowSvg,
  desiredScale = DEFAULT_PNG_SCALE,
): Promise<Blob> {
  const scale = exportScale(width, height, desiredScale);
  const image = new Image();
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error('The workflow diagram could not be rendered to an image.'));
    image.src = svgDataUrl(svg);
  });

  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('This browser could not provide a canvas to export with.');
  context.fillStyle = COLORS.page;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);

  const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('The workflow diagram could not be encoded as a PNG.');
  return blob;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function exportFileName(preferred: string | null, fallback: string, extension: string): string {
  const base = (preferred ?? fallback)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `${base || 'workflow'}.${extension}`;
}
