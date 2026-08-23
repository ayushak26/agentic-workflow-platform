/**
 * Pure model layer for the Business Chat experience.
 *
 * Business Chat turns a published workflow into a conversational business
 * capability: the first message runs the workflow through the existing
 * execution API, a Human Intervention node switches the conversation into a
 * structured approval card (never a plain text message), and follow-up
 * messages go to the existing Ask AI service with the run's context. This
 * module is pure (no fetch, no React) so every mapping rule is unit-tested —
 * see businessChatModel.test.ts.
 */
import yaml from 'js-yaml';

import type {
  HITLReviewContent,
  HITLReviewPanel,
  NodeRun,
  RunEvent,
  WorkflowFileReference,
} from '../../../api/types';
import {
  isWorkflowFileReference,
  normalizeChatOutputs,
  type ChatOutput,
} from './chatOutputs';

export type GenericChatExperienceId = 'general' | 'analyze' | 'research' | 'create';

export const GENERIC_CHAT_EXPERIENCES: Array<{
  id: GenericChatExperienceId;
  title: string;
  description: string;
}> = [
  {
    id: 'general',
    title: 'General',
    description: 'Ask anything. Chat chooses the best combination of reasoning, sources, and web research.',
  },
  {
    id: 'analyze',
    title: 'Analyze sources',
    description: 'Compare files, find patterns, surface risks, and identify contradictions.',
  },
  {
    id: 'research',
    title: 'Research',
    description: 'Investigate a topic deeply using available documents and current web information.',
  },
  {
    id: 'create',
    title: 'Create',
    description: 'Turn your request and sources into a report, presentation, brief, or other deliverable.',
  },
];

export type ChatFormField = {
  name: string;
  label: string;
  required: boolean;
  fieldType: string;
};

export type WorkflowChatMeta = {
  startMode: 'chatbot' | 'input_form' | 'none';
  chatbotName: string | null;
  welcomeMessage: string | null;
  suggestedQuestions: string[];
  formFields: ChatFormField[];
  /** node id → experience.display_name, for business-friendly progress. */
  displayNames: Record<string, string>;
  /** node id → author-written chat copy (experience.running_message /
   *  completed_message); takes priority over derived labels in chat. */
  runningMessages: Record<string, string>;
  completedMessages: Record<string, string>;
  nodes: WorkflowChatNode[];
  allowAttachments: boolean;
  capabilities: { web: boolean; tools: boolean; mcp: boolean; sources: boolean; models: boolean; images: boolean };
  declaredInputs?: string[];
};

export type WorkflowChatNode = {
  id: string;
  type: string;
  displayName: string;
  agentRole: string | null;
  purpose: string | null;
  recoveryActions: string[];
  config: Record<string, unknown>;
  allowedModels: string[];
  selectedModel: string | null;
  upstream: string[];
  downstream: string[];
};

export function businessActivityLabel(node: WorkflowChatNode): string | null {
  if (node.type === 'StartAgent' || node.type === 'EndAgent') return null;
  if (node.type === 'TransformAgent' && ['Prepare Answer', 'Chat Reply'].includes(node.displayName)) {
    return 'Prepared response';
  }
  if (node.type === 'WorkflowFileLoader') return 'Read attached sources';
  if (node.type === 'KnowledgeRetrieval') return 'Read knowledge sources';
  if (node.type === 'WebSearchAgent') return 'Searched the web';
  return node.displayName;
}

export type AgentActivity = {
  nodeId: string;
  nodeType: string;
  displayName: string;
  agentRole: string | null;
  status: 'waiting' | 'running' | 'completed' | 'failed' | 'needs_input' | 'reused';
  text: string;
  durationSeconds?: number | null;
  tool?: { kind: 'web' | 'mcp' | 'tool'; label: string; detail?: string };
  sources?: { title: string; url?: string }[];
  route?: { selected: string; reason?: string };
  image?: { key: string; contentType: string; provider: string; model?: string };
  error?: string;
  recoveryActions: string[];
};

/* eslint-disable @typescript-eslint/no-explicit-any */

export function chatMetaFromYaml(yamlText: string): WorkflowChatMeta {
  const meta: WorkflowChatMeta = {
    startMode: 'none',
    chatbotName: null,
    welcomeMessage: null,
    suggestedQuestions: [],
    formFields: [],
    displayNames: {},
    runningMessages: {},
    completedMessages: {},
    nodes: [],
    allowAttachments: false,
    capabilities: { web: false, tools: false, mcp: false, sources: false, models: false, images: false },
    declaredInputs: [],
  };
  let doc: any;
  try {
    doc = yaml.load(yamlText);
  } catch {
    return meta;
  }
  if (!doc || typeof doc !== 'object') return meta;
  meta.declaredInputs = doc.inputs && typeof doc.inputs === 'object' && !Array.isArray(doc.inputs)
    ? Object.keys(doc.inputs)
    : [];
  const nodes: any[] = Array.isArray(doc.nodes) ? doc.nodes : [];
  const upstream: Record<string, string[]> = {};
  const downstream: Record<string, string[]> = {};
  for (const edge of Array.isArray(doc.edges) ? doc.edges : []) {
    if (!edge || typeof edge.from !== 'string') continue;
    const targets = typeof edge.to === 'string'
      ? [edge.to]
      : Array.isArray(edge.to)
        ? edge.to.filter((item: unknown): item is string => typeof item === 'string')
        : edge.branches && typeof edge.branches === 'object'
          ? Object.values(edge.branches).filter((item): item is string => typeof item === 'string')
          : [];
    downstream[edge.from] = [...new Set([...(downstream[edge.from] ?? []), ...targets])];
    for (const target of targets) {
      upstream[target] = [...new Set([...(upstream[target] ?? []), edge.from])];
    }
  }
  for (const node of nodes) {
    if (!node || typeof node !== 'object' || typeof node.id !== 'string') continue;
    if (node.experience && typeof node.experience.display_name === 'string') {
      meta.displayNames[node.id] = node.experience.display_name;
    }
    if (node.experience && typeof node.experience.running_message === 'string'
      && node.experience.running_message !== '') {
      meta.runningMessages[node.id] = node.experience.running_message;
    }
    if (node.experience && typeof node.experience.completed_message === 'string'
      && node.experience.completed_message !== '') {
      meta.completedMessages[node.id] = node.experience.completed_message;
    }
    const type = typeof node.type === 'string' ? node.type : 'Node';
    const experience = node.experience && typeof node.experience === 'object'
      ? node.experience
      : {};
    meta.nodes.push({
      id: node.id,
      type,
      displayName: typeof experience.display_name === 'string'
        ? experience.display_name
        : humanizeNodeId(node.id),
      agentRole: typeof experience.agent_role === 'string' ? experience.agent_role : null,
      purpose: typeof experience.purpose === 'string' ? experience.purpose : null,
      recoveryActions: Array.isArray(experience.recovery_actions)
        ? experience.recovery_actions.filter((item: unknown): item is string => typeof item === 'string')
        : [],
      config: node.config && typeof node.config === 'object' ? node.config : {},
      allowedModels: Array.isArray(node.allowed_models)
        ? node.allowed_models.filter((item: unknown): item is string => typeof item === 'string')
        : [],
      selectedModel: typeof node.selected_model === 'string'
        ? node.selected_model
        : (typeof node.config?.model === 'string' ? node.config.model : null),
      upstream: upstream[node.id] ?? [],
      downstream: downstream[node.id] ?? [],
    });
    if (type === 'WebSearchAgent' || type === 'BoundedDeepResearchAgent') {
      meta.capabilities.web = true;
    }
    if (type === 'KnowledgeRetrieval') meta.capabilities.sources = true;
    if (type === 'OpenAIImageGenerationAgent' || type === 'DynamicFigureAgent') {
      meta.capabilities.images = true;
    }
    if (type === 'TransformAgent' && node.config?.mode !== 'deterministic') {
      meta.capabilities.models = true;
    }
    if (type === 'MCPAgent' || type === 'MCPToolAgent') {
      meta.capabilities.mcp = true;
      meta.capabilities.tools = true;
    } else if (type.includes('Integration') || type.includes('Tool')) {
      meta.capabilities.tools = true;
    }
    if (node.type !== 'StartAgent' || !node.config || typeof node.config !== 'object') {
      continue;
    }
    const config = node.config;
    if (config.mode === 'chatbot') {
      meta.startMode = 'chatbot';
      meta.chatbotName = typeof config.chatbot_name === 'string' ? config.chatbot_name : null;
      meta.welcomeMessage = typeof config.welcome_message === 'string'
        ? config.welcome_message
        : null;
      meta.suggestedQuestions = Array.isArray(config.suggested_questions)
        ? config.suggested_questions.filter((q: unknown) => typeof q === 'string')
        : [];
      meta.allowAttachments = config.allow_attachments !== false;
    } else if (config.mode === 'input_form') {
      meta.startMode = 'input_form';
      meta.formFields = Array.isArray(config.fields)
        ? config.fields
            .filter((f: any) => f && typeof f.name === 'string')
            .map((f: any) => ({
              name: f.name,
              label: typeof f.label === 'string' ? f.label : f.name,
              required: Boolean(f.required),
              fieldType: typeof f.type === 'string' ? f.type : 'string',
            }))
        : [];
    }
  }
  return meta;
}

/** Models accepted by every AI-mode Transform step. `auto` is always safe:
 * it is a routing sentinel and does not need to appear in allowed_models. */
export function compatibleTransformModels(meta: WorkflowChatMeta): string[] {
  const transforms = meta.nodes.filter(node => (
    node.type === 'TransformAgent' && node.config.mode !== 'deterministic'
  ));
  if (transforms.length === 0) return [];
  const constrained = transforms.filter(node => node.allowedModels.length > 0);
  if (constrained.length === 0) return [];
  return constrained
    .slice(1)
    .reduce(
      (common, node) => common.filter(model => node.allowedModels.includes(model)),
      [...constrained[0].allowedModels],
    );
}

/** Return an execution-only YAML copy with the chosen model applied only to
 * LLM-backed TransformAgent nodes. The saved workflow and deterministic
 * transforms are never modified. */
export function withTransformModel(yamlText: string, model: string): string {
  if (!model || model === 'workflow_default') return yamlText;
  const doc = yaml.load(yamlText) as Record<string, unknown> | null;
  if (!doc || !Array.isArray(doc.nodes)) return yamlText;
  const nodes = doc.nodes.map(raw => {
    if (!raw || typeof raw !== 'object') return raw;
    const node = raw as Record<string, unknown>;
    const config = asRecord(node.config);
    if (node.type !== 'TransformAgent' || config.mode === 'deterministic') return raw;
    const allowed = Array.isArray(node.allowed_models)
      ? node.allowed_models.filter((item): item is string => typeof item === 'string')
      : [];
    if (model !== 'auto' && allowed.length > 0 && !allowed.includes(model)) return raw;
    return { ...node, selected_model: model };
  });
  return yaml.dump({ ...doc, nodes }, { noRefs: true, lineWidth: 120 });
}

/** Map a chat message (+ structured form values) onto the workflow's
 *  declared inputs. Chatbot Start reads `message`; input_form Starts read
 *  their field names (the backend projects them to workflow inputs). */
export function buildRunInputs(
  meta: WorkflowChatMeta,
  message: string,
  formValues: Record<string, string>,
  attachments: WorkflowFileReference[] = [],
  optionalInputs: Record<string, unknown> = {},
): Record<string, unknown> {
  if (meta.startMode === 'chatbot') {
    const allowedOptionalInputs = Object.fromEntries(
      Object.entries(optionalInputs).filter(([name]) => (meta.declaredInputs ?? []).includes(name)),
    );
    return { message, ...(attachments.length > 0 ? { attachments } : {}), ...allowedOptionalInputs };
  }
  const inputs: Record<string, unknown> = {};
  for (const field of meta.formFields) {
    inputs[field.name] = formValues[field.name] ?? '';
  }
  return inputs;
}

function humanizeNodeId(value: string): string {
  return value
    .replace(/[._-]+/g, ' ')
    .replace(/\b\w/g, letter => letter.toUpperCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function meaningfulText(output: Record<string, unknown>): string | null {
  for (const key of ['chat_message', 'answer', 'summary', 'message', 'text', 'raw']) {
    const value = output[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  const parsed = asRecord(output.parsed);
  for (const key of ['answer', 'summary', 'message', 'recommendation']) {
    const value = parsed[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return null;
}

/** Universal adapter from heterogeneous durable node records to one chat
 * activity shape. Node-specific knowledge stays here, never in React. */
export function activityFromNodeRun(
  node: WorkflowChatNode,
  nodeRun: NodeRun | undefined,
  meta: WorkflowChatMeta,
): AgentActivity {
  const displayName = businessActivityLabel(node) ?? '';
  if (!nodeRun) {
    return {
      nodeId: node.id, nodeType: node.type, displayName,
      agentRole: node.agentRole, status: 'waiting', text: node.purpose ?? 'Waiting to start.',
      recoveryActions: node.recoveryActions,
    };
  }
  const output = asRecord(nodeRun.output);
  const base: AgentActivity = {
    nodeId: node.id,
    nodeType: node.type,
    displayName,
    agentRole: node.agentRole,
    status: nodeRun.status === 'paused' ? 'needs_input' : nodeRun.status,
    text: '',
    durationSeconds: nodeRun.duration_s,
    recoveryActions: node.recoveryActions,
  };
  if (nodeRun.status === 'running') {
    return { ...base, text: meta.runningMessages[node.id] ?? node.purpose ?? `Working on ${displayName || 'this step'}…` };
  }
  if (nodeRun.status === 'failed') {
    return {
      ...base,
      text: `${node.displayName} couldn't complete this step.`,
      error: nodeRun.error ?? 'This step failed.',
    };
  }
  if (node.type === 'WebSearchAgent') {
    const results = Array.isArray(output.results) ? output.results : [];
    const sources = results.flatMap(item => {
      const record = asRecord(item);
      return typeof record.title === 'string'
        ? [{ title: record.title, ...(typeof record.url === 'string' ? { url: record.url } : {}) }]
        : [];
    });
    const count = typeof output.result_count === 'number' ? output.result_count : sources.length;
    return {
      ...base,
      text: meta.completedMessages[node.id] ?? `I searched the web and found ${count} relevant source${count === 1 ? '' : 's'}.`,
      tool: { kind: 'web', label: 'Web Search', detail: `${count} sources` },
      sources,
    };
  }
  if (node.type === 'KnowledgeRetrieval') {
    const citations = Array.isArray(output.citations) ? output.citations : [];
    const sources = citations.flatMap(item => {
      const citation = asRecord(item);
      const title = citation.filename ?? citation.doc_title ?? citation.document_id;
      if (typeof title !== 'string' || !title) return [];
      const page = typeof citation.page === 'number' || typeof citation.page === 'string'
        ? ` · page ${citation.page}`
        : '';
      const section = typeof citation.section === 'string' && citation.section
        ? ` · ${citation.section}`
        : '';
      return [{ title: `${title}${page}${section}` }];
    });
    const count = typeof output.context_count === 'number' ? output.context_count : sources.length;
    return {
      ...base,
      text: meta.completedMessages[node.id]
        ?? `I retrieved ${count} relevant passage${count === 1 ? '' : 's'} from the connected knowledge sources.`,
      tool: { kind: 'tool', label: 'Knowledge Retrieval', detail: `${sources.length} citation${sources.length === 1 ? '' : 's'}` },
      sources,
    };
  }
  if (node.type === 'TransformAgent' && node.id === 'rewrite_query') {
    const parsed = asRecord(output.parsed);
    const rewritten = typeof parsed.retrieval_query === 'string' ? parsed.retrieval_query.trim() : '';
    return {
      ...base,
      text: rewritten ? `Retrieval query: ${rewritten}` : meta.completedMessages[node.id] ?? 'Prepared the Knowledge search query.',
      tool: { kind: 'tool', label: 'Knowledge query rewrite' },
    };
  }
  if (node.type === 'OpenAIImageGenerationAgent') {
    const generated = output.generated === true;
    const provider = typeof output.provider === 'string' ? output.provider : 'image provider';
    const key = typeof output.minio_key === 'string' ? output.minio_key : '';
    const contentType = typeof output.content_type === 'string' ? output.content_type : 'image/png';
    const model = typeof output.model === 'string' ? output.model : undefined;
    return {
      ...base,
      text: generated
        ? meta.completedMessages[node.id] ?? `I created an interview-preparation visual with ${provider}.`
        : `Image generation with ${provider} was skipped.`,
      ...(generated && key ? { image: { key, contentType, provider, model } } : {}),
      tool: { kind: 'tool', label: `${provider} image generation`, detail: model },
    };
  }
  if (node.type === 'MCPToolAgent') {
    const server = typeof output.server === 'string' ? output.server : 'Connected system';
    const tool = typeof output.tool === 'string' ? output.tool : 'tool';
    const failed = output.status === 'error' || output.status === 'denied';
    return {
      ...base,
      status: failed ? 'failed' : base.status,
      text: failed
        ? `${server} didn't complete the requested action.`
        : meta.completedMessages[node.id] ?? meaningfulText(output) ?? `${server} completed ${humanizeNodeId(tool)}.`,
      tool: { kind: 'mcp', label: server, detail: humanizeNodeId(tool) },
      ...(failed ? { error: typeof output.error === 'string' ? output.error : 'The connected system returned an error.' } : {}),
      recoveryActions: node.recoveryActions.length > 0
        ? node.recoveryActions
        : (output.retryable ? ['Retry this step', `Reconnect ${server}`, 'Continue without this source'] : []),
    };
  }
  if (node.type === 'MCPAgent') {
    const calls = Array.isArray(output.tool_calls) ? output.tool_calls.length : 0;
    return {
      ...base,
      text: meaningfulText(output) ?? meta.completedMessages[node.id] ?? `${node.displayName} completed its work.`,
      tool: { kind: 'mcp', label: 'MCP tools', detail: `${calls} call${calls === 1 ? '' : 's'}` },
    };
  }
  if (node.type === 'RouterAgent') {
    const selected = typeof output.route === 'string'
      ? output.route
      : Array.isArray(output.routes) ? output.routes.join(', ') : 'the next step';
    const reason = typeof output.reason === 'string' ? output.reason : undefined;
    return {
      ...base,
      text: reason ? `Routed to ${humanizeNodeId(selected)} because ${reason}` : `Routed to ${humanizeNodeId(selected)}.`,
      route: { selected, reason },
    };
  }
  return {
    ...base,
    text: meaningfulText(output)
      ?? meta.completedMessages[node.id]
      ?? `${node.displayName} completed this step.`,
  };
}

/** General Chat executes its saved workflow on every turn. Purpose-specific
 * workflows use run-context Ask AI after their first completed execution. */
export function resolveComposerIntent(
  hasCompletedRun: boolean,
  explicitMode: 'ask' | 'run' | 'auto',
  generalChat = false,
): 'ask' | 'run' {
  if (explicitMode === 'ask') return 'ask';
  if (explicitMode === 'run') return 'run';
  if (generalChat) return 'run';
  return hasCompletedRun ? 'ask' : 'run';
}

/** Map a runtime event to a business-friendly progress line, or null when
 *  the event carries nothing the user should see. Author-written copy
 *  (experience.running_message / completed_message, set in the Builder)
 *  wins; otherwise the workflow's experience.display_name is used — steps
 *  are never invented. */
export function eventProgressLabel(
  event: RunEvent,
  meta: WorkflowChatMeta,
): { key: string; text: string; done: boolean } | null {
  if (event.type === 'node_started') {
    const authored = meta.runningMessages[event.node_id];
    if (authored) return { key: event.node_id, text: authored, done: false };
    const label = meta.displayNames[event.node_id] ?? event.node_id;
    return { key: event.node_id, text: `Working on: ${label}…`, done: false };
  }
  if (event.type === 'node_completed' || event.type === 'node_reused') {
    const authored = meta.completedMessages[event.node_id];
    if (authored) return { key: event.node_id, text: authored, done: true };
    return {
      key: event.node_id,
      text: meta.displayNames[event.node_id] ?? event.node_id,
      done: true,
    };
  }
  if (event.type === 'run_failed' || event.type === 'run_rejected') {
    return { key: '__run__', text: event.error ?? 'The workflow stopped.', done: true };
  }
  return null;
}


// ---- Human Intervention ----------------------------------------------

/** Normalized intervention request for Chat's inline, business-first review
 * card. The actionable gate may belong to a subprocess child while the
 * conversation remains attached to the parent run. */
export type InterventionRequest = {
  gateId: string;
  runId: string;
  parentRunId: string | null;
  nodeId: string;
  question: string;
  reviewPurpose: string;
  context: Record<string, unknown>;
  allowedActions: string[];
  content: HITLReviewContent | null;
  panels: HITLReviewPanel[];
  allowDocumentOverride: boolean;
  maxEditChars: number;
  displayName: string;
};

export function interventionFromPendingGate(
  gate: {
    run_id: string;
    gate_id?: string;
    parent_run_id?: string | null;
    paused?: boolean;
    pause_kind?: string;
    node_id?: string | null;
    // The pending-gate API flattens the interrupt payload onto the gate
    // document (question/context/allowed_actions/…); tolerate a nested
    // `interrupt` object too for forward/backward compatibility.
    question?: unknown;
    review_purpose?: unknown;
    context?: unknown;
    allowed_actions?: unknown;
    content?: unknown;
    panels?: unknown;
    display_name?: unknown;
    allow_document_override?: unknown;
    max_edit_chars?: unknown;
    interrupt?: Record<string, unknown> | null;
  } | null | undefined,
  meta: WorkflowChatMeta,
): InterventionRequest | null {
  if (!gate || gate.paused === false) return null;
  // Cooperative pauses and internal subprocess waits are not review gates.
  if (gate.pause_kind === 'user_requested' || gate.pause_kind === 'subprocess') return null;
  const source: Record<string, unknown> = (
    gate.interrupt && typeof gate.interrupt === 'object' ? gate.interrupt : gate
  ) as Record<string, unknown>;
  if (source.kind === 'subprocess_pause') return null;
  const hasReviewContent = 'question' in source || 'context' in source
    || 'allowed_actions' in source;
  if (!hasReviewContent) return null;
  const nodeId = typeof source.node_id === 'string'
    ? source.node_id
    : (gate.node_id ?? '');
  return {
    gateId: typeof gate.gate_id === 'string' && gate.gate_id
      ? gate.gate_id
      : `${gate.run_id}:${nodeId}`,
    runId: gate.run_id,
    parentRunId: typeof gate.parent_run_id === 'string' ? gate.parent_run_id : null,
    nodeId,
    question: typeof source.question === 'string' && source.question !== ''
      ? source.question
      : 'Review required',
    reviewPurpose: typeof source.review_purpose === 'string' ? source.review_purpose : '',
    context: (source.context && typeof source.context === 'object'
      ? source.context
      : {}) as Record<string, unknown>,
    allowedActions: Array.isArray(source.allowed_actions)
      ? source.allowed_actions.filter((a): a is string => typeof a === 'string')
      : ['approve', 'reject', 'edit'],
    content: source.content && typeof source.content === 'object'
      ? source.content as HITLReviewContent
      : null,
    panels: Array.isArray(source.panels) ? source.panels as HITLReviewPanel[] : [],
    allowDocumentOverride: Boolean(source.allow_document_override ?? true),
    maxEditChars: typeof source.max_edit_chars === 'number' ? source.max_edit_chars : 1_000_000,
    displayName: typeof source.display_name === 'string' && source.display_name.trim()
      ? source.display_name
      : meta.displayNames[nodeId] ?? 'Human Review',
  };
}

/** The conversation is blocked on a review while an intervention is pending;
 *  the composer must not run the workflow or ask AI until it is resolved. */
export function composerDisabledReason(
  interventionPending: boolean,
  runInFlight: boolean,
): string | null {
  if (interventionPending) return 'Resolve the pending review to continue.';
  if (runInFlight) return 'The workflow is still working…';
  return null;
}

// ---- Output rendering --------------------------------------------------
// Primary visible output is normalized by chatOutputs.ts. Sources remain a
// secondary segment because they explain a response rather than being one.
export type ChatCitation = {
  number: number;
  title: string;
  snippet?: string;
  page?: number;
  section?: string;
  sourceUri?: string;
  documentId?: string;
  chunkId?: string;
  retrievalTraceId?: string;
  evidenceStatus?: string;
  sourceType?: 'webpage' | 'research_paper' | 'internal_document';
  downloadUrl?: string;
};
export type AssistantSegment = ChatOutput | { kind: 'sources'; items: ChatCitation[] };

export function isFileReference(value: unknown): value is WorkflowFileReference {
  return isWorkflowFileReference(value);
}

/** JSON → text by default: structured values render as pretty-printed JSON
 *  text so the chat stays readable without a raw-dump view. */
export function valueAsText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Node outputs are typed `unknown` at the API boundary — coerce safely. */
function outputAsRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Build the assistant message from a finished run's state. Chat-reply End
 *  nodes become text; files become image previews / document cards; other
 *  workflow outputs become labelled fields (JSON as text); citations come
 *  only from real source-producing node output — never fabricated. */
export function assistantSegments(run: {
  run_id?: string;
  outputs?: Record<string, unknown> | null;
  node_runs?: Record<string, { output?: unknown } | null> | null;
  node_types?: Record<string, string> | null;
}): AssistantSegment[] {
  const segments: AssistantSegment[] = normalizeChatOutputs(run);
  const nodeRuns = run.node_runs ?? {};
  const nodeTypes = run.node_types ?? {};

  // Sources come only from source-producing node output — never fabricated.
  const sourceItems: ChatCitation[] = [];
  const acquiredByCandidate = new Map<string, Record<string, unknown>>();
  for (const [nodeId, nodeRun] of Object.entries(nodeRuns)) {
    if (nodeTypes[nodeId] !== 'ResearchSourceAcquirer') continue;
    const documents = outputAsRecord(nodeRun?.output).documents;
    if (!Array.isArray(documents)) continue;
    for (const document of documents) {
      const record = outputAsRecord(document);
      if (typeof record.candidate_id === 'string') acquiredByCandidate.set(record.candidate_id, record);
    }
  }
  const sourcePriority: Record<string, number> = {
    WorkflowFileLoader: 0,
    WebSearchAgent: 1,
    MCPToolAgent: 2,
    BoundedDeepResearchAgent: 3,
    ScholarlyCandidateDiscoveryAgent: 3,
    KnowledgeRetrieval: 4,
    RAGAgent: 4,
  };
  const sourceNodeRuns = Object.entries(nodeRuns).sort(([leftId], [rightId]) => {
    const priority = (nodeId: string) => sourcePriority[nodeTypes[nodeId] ?? ''] ?? 99;
    return priority(leftId) - priority(rightId) || leftId.localeCompare(rightId);
  });
  for (const [nodeId, nodeRun] of sourceNodeRuns) {
    const nodeType = nodeTypes[nodeId];
    const output = outputAsRecord(nodeRun?.output);
    if (nodeType === 'WorkflowFileLoader') {
      const files = output.files;
      if (!Array.isArray(files)) continue;
      for (const file of files) {
        const record = outputAsRecord(file);
        if (typeof record.name !== 'string' || !record.name.trim()) continue;
        sourceItems.push({
          number: sourceItems.length + 1,
          title: record.name,
          evidenceStatus: typeof record.status === 'string' ? record.status : 'added_source',
          sourceType: 'internal_document',
        });
      }
      continue;
    }
    if (nodeType === 'WebSearchAgent') {
      const results = output.results;
      if (!Array.isArray(results)) continue;
      for (const result of results) {
        const record = outputAsRecord(result);
        const rawUrl = record.url;
        const sourceUri = typeof rawUrl === 'string' && /^https?:\/\//i.test(rawUrl)
          ? rawUrl
          : undefined;
        const title = typeof record.title === 'string' && record.title.trim()
          ? record.title
          : sourceUri;
        if (!title) continue;
        sourceItems.push({
          number: sourceItems.length + 1,
          title,
          ...(typeof record.snippet === 'string' && record.snippet ? { snippet: record.snippet } : {}),
          ...(sourceUri ? { sourceUri } : {}),
          evidenceStatus: 'candidate_only',
          sourceType: 'webpage',
        });
      }
      continue;
    }
    if (nodeType === 'MCPToolAgent' && output.server === 'paper-search-mcp' && output.tool === 'search_papers') {
      const data = outputAsRecord(output.data);
      const papers = Array.isArray(data.papers)
        ? data.papers
        : Array.isArray(data.results)
          ? data.results
          : Array.isArray(data.data) ? data.data : [];
      for (const paper of papers) {
        const record = outputAsRecord(paper);
        const rawDoi = record.doi ?? record.DOI;
        const rawUrl = record.canonical_url ?? record.url ?? record.pdf_url ?? (
          typeof rawDoi === 'string' ? `https://doi.org/${rawDoi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '')}` : undefined
        );
        const sourceUri = typeof rawUrl === 'string' && /^https?:\/\//i.test(rawUrl) ? rawUrl : undefined;
        const title = typeof record.title === 'string' && record.title.trim() ? record.title : sourceUri;
        if (!title) continue;
        const snippet = typeof record.abstract === 'string' && record.abstract
          ? record.abstract
          : typeof record.snippet === 'string' && record.snippet ? record.snippet : undefined;
        sourceItems.push({
          number: sourceItems.length + 1,
          title,
          ...(snippet ? { snippet } : {}),
          ...(sourceUri ? { sourceUri } : {}),
          evidenceStatus: 'candidate_only',
          sourceType: 'research_paper',
        });
      }
      continue;
    }
    if (nodeType === 'BoundedDeepResearchAgent' || nodeType === 'ScholarlyCandidateDiscoveryAgent') {
      const candidates = output.candidates;
      const dossiers = output.dossiers;
      const citedTextByUrl = new Map<string, string>();
      if (Array.isArray(dossiers)) {
        for (const dossier of dossiers) {
          const citations = outputAsRecord(dossier).citations;
          if (!Array.isArray(citations)) continue;
          for (const citation of citations) {
            const record = outputAsRecord(citation);
            if (typeof record.url === 'string' && typeof record.cited_text === 'string') {
              citedTextByUrl.set(record.url, record.cited_text);
            }
          }
        }
      }
      if (!Array.isArray(candidates)) continue;
      for (const candidate of candidates) {
        const record = outputAsRecord(candidate);
        const candidateId = typeof record.candidate_id === 'string' ? record.candidate_id : undefined;
        const acquired = candidateId ? acquiredByCandidate.get(candidateId) : undefined;
        const rawUrl = record.canonical_url ?? record.pdf_url ?? (
          typeof record.doi === 'string' ? `https://doi.org/${record.doi}` : undefined
        );
        const sourceUri = typeof rawUrl === 'string' && /^https?:\/\//i.test(rawUrl) ? rawUrl : undefined;
        const title = typeof record.title === 'string' && record.title.trim() ? record.title : sourceUri;
        if (!title) continue;
        const sourceName = typeof record.source === 'string' ? record.source.toLowerCase() : '';
        const identifiers = outputAsRecord(record.canonical_identifiers);
        const paper = Boolean(record.doi || record.paper_id || identifiers.doi || identifiers.pmid
          || identifiers.pmcid || identifiers.arxiv_id || ['arxiv', 'openalex', 'pubmed', 'semantic_scholar', 'europepmc'].includes(sourceName));
        const documentId = typeof acquired?.document_id === 'string' ? acquired.document_id : undefined;
        const pdfKey = typeof acquired?.pdf_object_key === 'string' ? acquired.pdf_object_key : undefined;
        const downloadUrl = run.run_id && documentId && pdfKey?.toLowerCase().endsWith('.pdf')
          ? `/api/candidates/${encodeURIComponent(run.run_id)}/documents/${encodeURIComponent(documentId)}/download`
          : undefined;
        const snippet = (sourceUri && citedTextByUrl.get(sourceUri))
          || (typeof record.abstract === 'string' ? record.abstract : undefined);
        sourceItems.push({
          number: sourceItems.length + 1,
          title,
          ...(snippet ? { snippet } : {}),
          ...(sourceUri ? { sourceUri } : {}),
          ...(documentId ? { documentId } : {}),
          ...(downloadUrl ? { downloadUrl } : {}),
          evidenceStatus: acquired ? 'acquired_full_text' : 'candidate_only',
          sourceType: paper ? 'research_paper' : 'webpage',
        });
      }
      continue;
    }
    if (nodeType !== 'KnowledgeRetrieval' && nodeType !== 'RAGAgent') continue;
    const citations = output.citations;
    const chunks = output.retrieved_chunks ?? output.retrievals ?? output.relevant_context;
    const retrievalTraceId = typeof output.retrieval_trace_id === 'string' && output.retrieval_trace_id
      ? output.retrieval_trace_id
      : undefined;
    if (!Array.isArray(citations)) continue;
    const chunkList = Array.isArray(chunks) ? chunks.map(outputAsRecord) : [];
    for (const [citationIndex, citation] of citations.entries()) {
      if (citation && typeof citation === 'object') {
        const record = citation as Record<string, unknown>;
        const name = record.filename ?? record.doc_title ?? record.source_doc ?? record.document_id;
        if (typeof name !== 'string' || name === '') continue;
        const chunk = chunkList.find(item => item.chunk_id === record.chunk_id) ?? {};
        const metadata = outputAsRecord(chunk.metadata);
        const snippet = chunk.compressed_text ?? chunk.context_content ?? chunk.text;
        const rawSourceUri = metadata.source_uri ?? metadata.url;
        const sourceUri = typeof rawSourceUri === 'string' && /^https?:\/\//i.test(rawSourceUri)
          ? rawSourceUri
          : undefined;
        sourceItems.push({
          number: typeof chunk.display_number === 'number' ? chunk.display_number : citationIndex + 1,
          title: name,
          ...(typeof snippet === 'string' && snippet ? { snippet } : {}),
          ...(typeof record.page === 'number' ? { page: record.page } : {}),
          ...(typeof record.section === 'string' && record.section ? { section: record.section } : {}),
          ...(sourceUri ? { sourceUri } : {}),
          ...(typeof record.document_id === 'string' ? { documentId: record.document_id } : {}),
          ...(typeof record.chunk_id === 'string' ? { chunkId: record.chunk_id } : {}),
          ...(retrievalTraceId ? { retrievalTraceId } : {}),
          ...(typeof record.evidence_status === 'string' ? { evidenceStatus: record.evidence_status } : {}),
          sourceType: 'internal_document',
        });
      }
    }
  }
  const uniqueSources = new Map<string, ChatCitation>();
  for (const item of sourceItems) {
    const key = item.sourceUri ?? item.chunkId ?? item.documentId ?? item.title;
    if (!uniqueSources.has(key)) uniqueSources.set(key, item);
  }
  const deduped = [...uniqueSources.values()].map((item, index) => ({
    ...item,
    number: index + 1,
  }));
  if (deduped.length > 0) segments.push({ kind: 'sources', items: deduped });

  return segments;
}

export function structuredResultFromRun(run: {
  outputs?: Record<string, unknown> | null;
  node_runs?: Record<string, { output?: unknown } | null> | null;
}): unknown | null {
  const directHandoff = outputAsRecord(run.outputs?.handoff);
  if ('structured_result' in directHandoff) return directHandoff.structured_result;
  for (const nodeRun of Object.values(run.node_runs ?? {})) {
    const result = outputAsRecord(outputAsRecord(nodeRun?.output).result);
    const handoff = outputAsRecord(result.handoff);
    if ('structured_result' in handoff) return handoff.structured_result;
  }
  return null;
}
