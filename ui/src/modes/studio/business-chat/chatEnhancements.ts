export type ResponseFormat = 'auto' | 'prose' | 'bullets' | 'numbered' | 'table' | 'chart';
export type WritingStyle = 'concise' | 'detailed' | 'academic' | 'casual' | 'executive' | 'bullet-first';

export type SlashCommand = {
  command: string;
  label: string;
  description: string;
  format?: ResponseFormat;
  insertion?: string;
  action?: 'templates' | 'workflows' | 'research';
};

export const SLASH_COMMANDS: SlashCommand[] = [
  { command: '/table', label: 'Table', description: 'Format this response as a table.', format: 'table' },
  { command: '/chart', label: 'Chart', description: 'Prefer a chart for quantitative data.', format: 'chart' },
  { command: '/summarize', label: 'Summarize', description: 'Create a concise summary.', insertion: 'Summarize ' },
  { command: '/template', label: 'Prompt template', description: 'Open the prompt templates library.', action: 'templates' },
  { command: '/workflow', label: 'Workflow', description: 'Return to the workflow catalog.', action: 'workflows' },
  { command: '/research', label: 'Deep research', description: 'Open bounded deep research across web pages and research papers.', action: 'research' },
  { command: '/prose', label: 'Prose', description: 'Use paragraphs and prose.', format: 'prose' },
  { command: '/bullets', label: 'Bullets', description: 'Use a concise bullet list.', format: 'bullets' },
  { command: '/brief', label: 'Executive brief', description: 'Write a concise executive brief.', insertion: 'Provide a concise executive brief: ' },
];

export function matchingSlashCommands(text: string): SlashCommand[] {
  if (!text.startsWith('/') || text.includes(' ')) return [];
  return SLASH_COMMANDS.filter(item => item.command.startsWith(text.toLowerCase()));
}

export function extractTemplateVariables(content: string): string[] {
  const found = [...content.matchAll(/{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}/g)].map(match => match[1]);
  return [...new Set(found)];
}

export function renderPromptTemplate(content: string, values: Record<string, string>): string {
  return content.replace(/{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}/g, (_match, name: string) => (
    values[name]?.trim() || `{{${name}}}`
  ));
}

export function classifyResponseFormat(query: string, answer = ''): Exclude<ResponseFormat, 'auto'> {
  const text = `${query} ${answer}`.toLowerCase();
  const numericSignals = (text.match(/\b\d+(?:\.\d+)?%?\b/g) ?? []).length;
  if (/\b(chart|graph|trend|distribution|correlation)\b/.test(text) && numericSignals >= 2) return 'chart';
  if (/\b(compare|comparison|versus|\bvs\.?\b|matrix)\b/.test(text)) return 'table';
  if (/\b(steps?|procedure|how (?:do|to)|sequence|rank(?:ing)?)\b/.test(text)) return 'numbered';
  if (/\b(list|options|features|pros and cons|advantages|disadvantages)\b/.test(text)) return 'bullets';
  return 'prose';
}

export function formatHint(format: ResponseFormat, style: WritingStyle): string {
  const formatInstruction: Record<ResponseFormat, string> = {
    auto: 'Choose the clearest format for the content and avoid over-formatting simple answers.',
    prose: 'Respond in clear paragraphs and prose.',
    bullets: 'Respond as a concise unordered bullet list.',
    numbered: 'Respond as a numbered sequential list.',
    table: 'Respond as a Markdown comparison table where the data supports it.',
    chart: 'Organize quantitative data for a chart and include a readable text fallback.',
  };
  return `[Response preference: ${formatInstruction[format]} Writing style: ${style}.]`;
}

export function followUpExecutionOutput(text: string): 'pdf' | 'pptx' | null {
  if (!/\b(create|make|generate|export|turn|convert)\b/i.test(text)) return null;
  if (/\b(slides?|presentation|deck|powerpoint|pptx)\b/i.test(text)) return 'pptx';
  if (/\b(pdf|report|document)\b/i.test(text)) return 'pdf';
  return null;
}

export function applySlashCommand(command: SlashCommand, currentText: string): {
  text: string;
  format?: ResponseFormat;
  action?: SlashCommand['action'];
} {
  const remainder = currentText.slice(command.command.length).trimStart();
  return {
    text: `${command.insertion ?? ''}${remainder}`,
    ...(command.format ? { format: command.format } : {}),
    ...(command.action ? { action: command.action } : {}),
  };
}