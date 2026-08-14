/**
 * How a step is named on the canvas.
 *
 * Shared between the live React Flow node (WorkflowNode) and the standalone
 * PNG/SVG export (graph-export) so an exported diagram reads exactly like the
 * canvas it was taken from — the export is a deliverable people paste into
 * documents and decks, and a diagram that labels steps differently from the
 * builder is worse than no diagram.
 */

// Business-language names for the core primitives, matching the palette.
export const TYPE_LABELS: Record<string, string> = {
  WorkflowInputAgent: 'Input',
  AITaskAgent: 'AI Task',
  DecisionAgent: 'Decision',
  RouterAgent: 'Router',
  DataTransformAgent: 'Transform',
  HumanInLoopAgent: 'Human Review',
  EmailAgent: 'Email',
  MCPToolAgent: 'MCP Tool',
};

/**
 * The subtitle under a step's headline. An MCP step says which system it
 * reaches, because "MCP Tool" alone tells a reader nothing: "MCP Tool ·
 * Dynamics CRM" is the useful subtitle.
 */
export function nodeTypeLabel(typeName: string, config: Record<string, unknown>): string {
  const base = TYPE_LABELS[typeName] ?? typeName;
  const serverId = typeof config.server_id === 'string' ? config.server_id : '';
  return serverId ? `${base} · ${serverId}` : base;
}
