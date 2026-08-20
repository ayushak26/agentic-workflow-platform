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
  StartAgent: 'Start',
  EndAgent: 'End',
  WorkflowInputAgent: 'Input',
  AITaskAgent: 'AI Task',
  DecisionAgent: 'Decision',
  RouterAgent: 'Router',
  DataTransformAgent: 'Transform',
  HumanInLoopAgent: 'Human Review',
  EmailAgent: 'Email',
  IntegrationAgent: 'Integration',
  MCPToolAgent: 'MCP Tool',
  ExternalActionAgent: 'External Action',
  SubprocessAgent: 'Subprocess',
  SQLQueryAgent: 'SQL Query',
  PythonSnippetAgent: 'Code Snippet',
  RAGAgent: 'RAG Agent',
};

/**
 * The subtitle under a step's headline. An MCP step says which system it
 * reaches, because "MCP Tool" alone tells a reader nothing: "MCP Tool ·
 * Dynamics CRM" is the useful subtitle.
 */
const INTEGRATION_PROVIDER_LABELS: Record<string, string> = {
  google_drive: 'Google Drive',
  onedrive: 'OneDrive',
};

function humanizeToolName(tool: string): string {
  return tool
    .split('_')
    .filter(Boolean)
    .map(word => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

export function nodeTypeLabel(typeName: string, config: Record<string, unknown>): string {
  const base = typeName === 'RouterAgent' && config.selection === 'multi'
    ? 'Multi-Route'
    : TYPE_LABELS[typeName] ?? typeName;
  const serverId = typeof config.server_id === 'string' ? config.server_id : '';
  if (typeName === 'MCPToolAgent') {
    const tool = typeof config.tool === 'string' ? config.tool : '';
    if (serverId && tool) return `${base} · ${serverId} · ${humanizeToolName(tool)}`;
    if (serverId) return `${base} · ${serverId}`;
    return base;
  }
  if (serverId) return `${base} · ${serverId}`;
  if (typeName === 'IntegrationAgent' && typeof config.provider === 'string') {
    const provider = INTEGRATION_PROVIDER_LABELS[config.provider] ?? config.provider;
    return `${base} · ${provider}`;
  }
  // Cosmetic only — a self-healing cache written whenever the Configure tab
  // last resolved the agent's live name (see RAGAgentConfig.tsx). Execution
  // always keys off config.rag_agent_id, never this label.
  if (typeName === 'RAGAgent' && typeof config.rag_agent_name === 'string' && config.rag_agent_name) {
    return `${base} · ${config.rag_agent_name}`;
  }
  if (typeName === 'StartAgent') {
    if (config.mode === 'chatbot') return `${base} · Chatbot Interface`;
    const count = Array.isArray(config.fields) ? config.fields.length : 0;
    const fileCount = Array.isArray(config.file_fields) ? config.file_fields.length : 0;
    const total = count + fileCount;
    return `${base} · Input Form${total ? ` · ${total} field${total === 1 ? '' : 's'}` : ''}`;
  }
  if (typeName === 'EndAgent') {
    if (config.mode === 'chat_response') return `${base} · Chat Response`;
    if (config.mode === 'custom_response') return `${base} · Custom Response`;
    const count = Array.isArray(config.outputs) ? config.outputs.length : 0;
    return `${base} · Workflow Result${count ? ` · ${count} output${count === 1 ? '' : 's'}` : ''}`;
  }
  return base;
}
