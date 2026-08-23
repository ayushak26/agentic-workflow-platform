import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';
import yaml from 'js-yaml';

import type { RunEvent, WorkflowFileReference, WorkflowSummary } from '../../../api/types';
import {
  assistantSegments,
  activityFromNodeRun,
  buildRunInputs,
  chatEligibleWorkflows,
  chatMetaFromYaml,
  compatibleTransformModels,
  composerDisabledReason,
  eventProgressLabel,
  interventionFromPendingGate,
  isFileReference,
  resolveComposerIntent,
  valueAsText,
  withTransformModel,
  type WorkflowChatMeta,
} from './businessChatModel';

// Node-side test reading the repo's reference corpus (same convention as
// yaml-roundtrip.shipped.test.ts; excluded from the DOM-scoped tsconfig).
// Vitest's configured root is ui/, while import.meta.url uses an http: URL in
// jsdom, so resolve from that stable process working directory rather than
// attempting fileURLToPath(import.meta.url).
const REFERENCE_DIR = resolve(process.cwd(), '../workflows/reference');

const fileRef = (overrides: Partial<WorkflowFileReference> = {}): WorkflowFileReference => ({
  kind: 'workflow_file',
  file_id: 'f1',
  name: 'chart.png',
  extension: '.png',
  category: 'image',
  content_type: 'image/png',
  size_bytes: 10,
  sha256: 'x',
  minio_key: 'k',
  parseable_text: false,
  ...overrides,
});

const metaDefaults = {
  nodes: [],
  allowAttachments: true,
  capabilities: { web: false, tools: false, mcp: false, sources: false, models: false, images: false },
};

describe('chatEligibleWorkflows', () => {
  it('only offers Library-approved workflows', () => {
    const approved = { name: 'a' } as unknown as WorkflowSummary;
    approved.library = { visibility_status: 'approved' } as WorkflowSummary['library'];
    const draft = { name: 'b' } as unknown as WorkflowSummary;
    draft.library = { visibility_status: 'draft' } as WorkflowSummary['library'];
    const bare = { name: 'c' } as unknown as WorkflowSummary;
    expect(chatEligibleWorkflows([approved, draft, bare]).map(w => w.name)).toEqual(['a']);
  });
});

describe('chatMetaFromYaml', () => {
  it('extracts the chatbot Start experience from the reference QA workflow', () => {
    const yamlText = readFileSync(resolve(REFERENCE_DIR, 'ref_chat_knowledge_qa.yaml'), 'utf8');
    const meta = chatMetaFromYaml(yamlText);
    expect(meta.startMode).toBe('chatbot');
    expect(meta.chatbotName).toBe('Knowledge Assistant');
    expect(meta.welcomeMessage).toContain('knowledge base');
    expect(meta.suggestedQuestions).toContain('What is our cancellation policy?');
    expect(meta.displayNames.search_knowledge).toBe('Search Knowledge');
    expect(meta.displayNames.draft_answer).toBe('Draft Grounded Answer');
    // Builder-authored chat copy flows through the same experience block.
    expect(meta.runningMessages.search_knowledge).toBe('Searching the knowledge base…');
    expect(meta.completedMessages.draft_answer).toBe('Response drafted');
  });

  it('extracts input_form fields from the reference triage workflow', () => {
    const yamlText = readFileSync(resolve(REFERENCE_DIR, 'ref_knowledge_triage.yaml'), 'utf8');
    const meta = chatMetaFromYaml(yamlText);
    expect(meta.startMode).toBe('input_form');
    expect(meta.formFields).toEqual([
      { name: 'request', label: 'Request', required: true, fieldType: 'string' },
    ]);
  });

  it('survives broken YAML without throwing', () => {
    const meta = chatMetaFromYaml(':\n  - not yaml [');
    expect(meta.startMode).toBe('none');
  });
});

describe('buildRunInputs', () => {
  const chatbotMeta: WorkflowChatMeta = {
    startMode: 'chatbot', chatbotName: null, welcomeMessage: null,
    suggestedQuestions: [], formFields: [], displayNames: {},
    runningMessages: {}, completedMessages: {},
    ...metaDefaults,
  };
  it('maps the message onto the chatbot Start input', () => {
    expect(buildRunInputs(chatbotMeta, 'What is our cancellation policy?', {}))
      .toEqual({ message: 'What is our cancellation policy?' });
  });
  it('maps form fields by name for input_form Starts', () => {
    const meta = chatMetaFromYaml(
      readFileSync(resolve(REFERENCE_DIR, 'ref_knowledge_triage.yaml'), 'utf8'),
    );
    expect(buildRunInputs(meta, '', { request: 'Need a refund' }))
      .toEqual({ request: 'Need a refund' });
  });
});

describe('resolveComposerIntent', () => {
  it('first message runs the workflow; follow-ups ask AI', () => {
    expect(resolveComposerIntent(false, 'auto')).toBe('run');
    expect(resolveComposerIntent(true, 'auto')).toBe('ask');
  });
  it('explicit modes always win', () => {
    expect(resolveComposerIntent(true, 'run')).toBe('run');
    expect(resolveComposerIntent(false, 'ask')).toBe('ask');
  });
});

describe('eventProgressLabel', () => {
  const meta: WorkflowChatMeta = {
    startMode: 'chatbot', chatbotName: null, welcomeMessage: null,
    suggestedQuestions: [], formFields: [],
    displayNames: { search_knowledge: 'Search Knowledge' },
    runningMessages: { search_knowledge: 'Searching the knowledge base…' },
    completedMessages: { search_knowledge: 'Relevant passages found' },
    ...metaDefaults,
  };
  const started: RunEvent = {
    type: 'node_started', run_id: 'r', node_id: 'search_knowledge', ts: 't',
  };
  it('uses experience.display_name for business-friendly progress', () => {
    const plainMeta: WorkflowChatMeta = { ...meta, runningMessages: {}, completedMessages: {} };
    expect(eventProgressLabel(started, plainMeta)).toEqual({
      key: 'search_knowledge', text: 'Working on: Search Knowledge…', done: false,
    });
  });
  it('prefers Builder-authored chat copy when present', () => {
    expect(eventProgressLabel(started, meta)).toEqual({
      key: 'search_knowledge', text: 'Searching the knowledge base…', done: false,
    });
    const completed: RunEvent = {
      type: 'node_completed', run_id: 'r', node_id: 'search_knowledge',
      output_preview: '', ts: 't',
    };
    expect(eventProgressLabel(completed, meta)).toEqual({
      key: 'search_knowledge', text: 'Relevant passages found', done: true,
    });
  });
  it('falls back to the node id when no display name exists', () => {
    const unknown: RunEvent = { ...started, node_id: 'llm_7' };
    expect(eventProgressLabel(unknown, meta)?.text).toBe('Working on: llm_7…');
  });
  it('ignores run_completed (the assistant message carries the result)', () => {
    const completed: RunEvent = { type: 'run_completed', run_id: 'r', ts: 't' };
    expect(eventProgressLabel(completed, meta)).toBeNull();
  });

describe('interventionFromPendingGate', () => {
  const meta: WorkflowChatMeta = {
    startMode: 'chatbot', chatbotName: null, welcomeMessage: null,
    suggestedQuestions: [], formFields: [],
    displayNames: { review: 'Human Review' },
    runningMessages: {}, completedMessages: {},
    ...metaDefaults,
  };
  it('normalizes a durable HITL gate payload (flattened API shape)', () => {
    const request = interventionFromPendingGate({
      run_id: 'run-1',
      paused: true,
      pause_kind: 'hitl_gate',
      node_id: 'review',
      question: 'Approve the drafted answer?',
      review_purpose: 'Customer-facing answers need a human check.',
      context: { 'draft.parsed.draft': 'Hello…' },
      allowed_actions: ['approve', 'edit', 'reject'],
      content: { text: 'Hello…', format: 'text', source: 'workflow' },
      allow_document_override: false,
      max_edit_chars: 5000,
    }, meta);
    expect(request).not.toBeNull();
    expect(request?.runId).toBe('run-1');
    expect(request?.nodeId).toBe('review');
    expect(request?.question).toBe('Approve the drafted answer?');
    expect(request?.allowedActions).toEqual(['approve', 'edit', 'reject']);
    expect(request?.allowDocumentOverride).toBe(false);
    expect(request?.maxEditChars).toBe(5000);
    expect(request?.displayName).toBe('Human Review');
  });
  it('also accepts a nested interrupt payload', () => {
    const request = interventionFromPendingGate({
      run_id: 'run-2',
      paused: true,
      pause_kind: 'hitl_gate',
      interrupt: {
        node_id: 'review',
        question: 'Approve?',
        allowed_actions: ['approve'],
        context: {},
      },
    }, meta);
    expect(request?.nodeId).toBe('review');
    expect(request?.allowedActions).toEqual(['approve']);
  });
  it('is null when the run is not paused', () => {
    expect(interventionFromPendingGate({ run_id: 'r', paused: false }, meta)).toBeNull();
  });
  it('is null for cooperative user-requested pauses (not review gates)', () => {
    expect(interventionFromPendingGate(
      { run_id: 'r', paused: true, pause_kind: 'user_requested', node_id: 'x' },
      meta,
    )).toBeNull();
  });
  it('is null when the checkpoint carries no interrupt payload', () => {
    expect(interventionFromPendingGate(
      { run_id: 'r', paused: true, pause_kind: 'hitl_gate', node_id: 'x' },
      meta,
    )).toBeNull();
  });
});

describe('composerDisabledReason', () => {
  it('blocks while a review is pending, then while running, else null', () => {
    expect(composerDisabledReason(true, true)).toContain('review');
    expect(composerDisabledReason(false, true)).toContain('working');
    expect(composerDisabledReason(false, false)).toBeNull();
  });
});

describe('assistantSegments', () => {
  it('renders normalized chat text, supported artifacts, fallbacks, and citations', () => {
    const segments = assistantSegments({
      outputs: {
        risk: 'Medium',
        brief: { summary: 'ok', items: [1, 2] },
        chart: fileRef(),
        report: fileRef({
          file_id: 'f2', name: 'notes.txt', extension: '.txt',
          category: 'document', content_type: 'text/plain', parseable_text: true,
        }),
      },
      node_runs: {
        reply: { output: { chat_message: 'Contoso has expanded…' } },
        search: {
          output: {
            citations: [
              { filename: 'Handbook.pdf', document_id: 'd1' },
              { filename: 'Handbook.pdf', document_id: 'd1' },
            ],
          },
        },
      },
      node_types: { reply: 'EndAgent', search: 'KnowledgeRetrieval' },
    });
    const kinds = segments.map(s => s.kind);
    expect(kinds).toEqual(['text', 'image', 'text', 'text', 'text', 'sources']);
    expect(segments[0]).toEqual({ kind: 'text', text: 'Contoso has expanded…' });
    expect(segments[1]).toEqual(expect.objectContaining({ kind: 'image', title: 'chart.png' }));
    expect(segments[2]).toEqual({ kind: 'text', text: 'Risk: Medium' });
    expect(segments[3]).toEqual({ kind: 'text', text: 'Brief\nSummary: ok\nItems: 2 items' });
    // Unsupported file references remain a readable text fallback instead of
    // becoming a new primary output kind.
    expect(segments[4]).toEqual(expect.objectContaining({ kind: 'text' }));
    expect(segments[5]).toEqual({ kind: 'sources', items: ['Handbook.pdf'] });
  });

  it('never fabricates sources without a KnowledgeRetrieval node', () => {
    const segments = assistantSegments({
      outputs: { answer: 'hi' },
      node_runs: { e: { output: { chat_message: 'hi' } } },
      node_types: { e: 'EndAgent' },
    });
    expect(segments.some(s => s.kind === 'sources')).toBe(false);
  });
});

describe('universal agent activity projection', () => {
  const workflowMeta = chatMetaFromYaml(`
name: demo
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      allow_attachments: true
  - id: research
    type: WebSearchAgent
    experience:
      display_name: Research Agent
      agent_role: Researcher
  - id: route
    type: RouterAgent
    experience:
      display_name: Router
  - id: crm
    type: MCPToolAgent
    experience:
      display_name: Salesforce Agent
      recovery_actions: [Retry this step, Reconnect Salesforce]
  - id: knowledge
    type: KnowledgeRetrieval
    config:
      collection_id: handbook
      retrieval_profile_id: balanced
      query: test
edges:
  - from: start
    to: research
  - from: research
    to: route
  - from: route
    to: crm
  - from: crm
    to: knowledge
`);

  it('extracts topology, identity, and contextual capabilities', () => {
    expect(workflowMeta.nodes[1]).toMatchObject({
      id: 'research', displayName: 'Research Agent', agentRole: 'Researcher', downstream: ['route'],
    });
    expect(workflowMeta.nodes[2].upstream).toEqual(['research']);
    expect(workflowMeta.capabilities).toEqual({
      web: true, tools: true, mcp: true, sources: true, models: false, images: false,
    });
  });

  it('projects web search output into compact tool activity with real sources', () => {
    const activity = activityFromNodeRun(workflowMeta.nodes[1], {
      node_id: 'research', type_name: 'WebSearchAgent', status: 'completed',
      input: {}, output: { result_count: 2, results: [
        { title: 'Source A', url: 'https://example.com/a' },
        { title: 'Source B', url: 'https://example.com/b' },
      ] }, started_at: 1, ended_at: 2, duration_s: 1, error: null,
    }, workflowMeta);
    expect(activity.tool).toEqual({ kind: 'web', label: 'Web Search', detail: '2 sources' });
    expect(activity.sources).toHaveLength(2);
    expect(activity.displayName).toBe('Research Agent');
  });

  it('projects routes conversationally and MCP failures with recovery actions', () => {
    const route = activityFromNodeRun(workflowMeta.nodes[2], {
      node_id: 'route', type_name: 'RouterAgent', status: 'completed', input: {},
      output: { route: 'competitive_research', reason: 'external information is required' },
      started_at: 1, ended_at: 2, duration_s: 1, error: null,
    }, workflowMeta);
    expect(route.text).toContain('Routed to Competitive Research because external information is required');

    const failure = activityFromNodeRun(workflowMeta.nodes[3], {
      node_id: 'crm', type_name: 'MCPToolAgent', status: 'completed', input: {},
      output: { server: 'Salesforce', tool: 'get_opportunities', status: 'error', error: 'timeout', retryable: true },
      started_at: 1, ended_at: 3, duration_s: 2, error: null,
    }, workflowMeta);
    expect(failure.status).toBe('failed');
    expect(failure.text).toBe("Salesforce didn't complete the requested action.");
    expect(failure.recoveryActions).toEqual(['Retry this step', 'Reconnect Salesforce']);
  });

  it('passes uploaded references through the chatbot Start attachment contract', () => {
    expect(buildRunInputs(workflowMeta, 'Review this', {}, [fileRef()])).toEqual({
      message: 'Review this', attachments: [fileRef()],
    });
  });

  it('projects only real Knowledge Retrieval citations as sources', () => {
    const knowledge = workflowMeta.nodes.find(node => node.id === 'knowledge');
    expect(knowledge).toBeDefined();
    const activity = activityFromNodeRun(knowledge!, {
      node_id: 'knowledge', type_name: 'KnowledgeRetrieval', status: 'completed', input: {},
      output: {
        context_count: 2,
        citations: [
          { filename: 'Handbook.pdf', page: 4, section: 'Refunds' },
          { filename: 'Terms.docx', page: 2 },
        ],
      },
      started_at: 1, ended_at: 2, duration_s: 1, error: null,
    }, workflowMeta);
    expect(activity.tool).toEqual({ kind: 'tool', label: 'Knowledge Retrieval', detail: '2 citations' });
    expect(activity.sources).toEqual([
      { title: 'Handbook.pdf · page 4 · Refunds' },
      { title: 'Terms.docx · page 2' },
    ]);
  });
});

describe('Transform model choices', () => {
  const workflow = `
name: model-demo
nodes:
  - id: answer
    type: TransformAgent
    allowed_models: [auto, claude-sonnet-4-5, gpt-5.6-sol]
    config:
      mode: ai
      instructions: Answer the question.
  - id: format
    type: TransformAgent
    config:
      mode: deterministic
      operations:
        - operation: copy
          target: result
          value: done
edges:
  - from: answer
    to: format
`;

  it('finds compatible model choices and marks the workflow as model-capable', () => {
    const meta = chatMetaFromYaml(workflow);
    expect(meta.capabilities.models).toBe(true);
    expect(compatibleTransformModels(meta)).toEqual(['auto', 'claude-sonnet-4-5', 'gpt-5.6-sol']);
  });

  it('overrides only LLM-backed Transform agents in an execution copy', () => {
    const result = yaml.load(withTransformModel(workflow, 'gpt-5.6-sol')) as {
      nodes: Array<Record<string, unknown>>;
    };
    expect(result.nodes[0].selected_model).toBe('gpt-5.6-sol');
    expect(result.nodes[1].selected_model).toBeUndefined();
  });

  it('does not force a disallowed model and leaves workflow-default YAML untouched', () => {
    const disallowed = yaml.load(withTransformModel(workflow, 'not-allowed')) as {
      nodes: Array<Record<string, unknown>>;
    };
    expect(disallowed.nodes[0].selected_model).toBeUndefined();
    expect(withTransformModel(workflow, 'workflow_default')).toBe(workflow);
  });
});

describe('image generation activity', () => {
  it('projects OpenAI and OpenRouter image outputs as inline artifacts', () => {
    const meta = chatMetaFromYaml(`
name: images
nodes:
  - id: visual
    type: OpenAIImageGenerationAgent
    config:
      backend: openrouter
      prompt: a visual
`);
    expect(meta.capabilities.images).toBe(true);
    const activity = activityFromNodeRun(meta.nodes[0], {
      node_id: 'visual', type_name: 'OpenAIImageGenerationAgent', status: 'completed', input: {},
      output: {
        generated: true,
        provider: 'openrouter',
        model: 'google/gemini-3.1-flash-image',
        minio_key: 'workflows/r/images/visual.png',
        content_type: 'image/png',
      },
      started_at: 1, ended_at: 3, duration_s: 2, error: null,
    }, meta);
    expect(activity.image).toEqual({
      key: 'workflows/r/images/visual.png',
      contentType: 'image/png',
      provider: 'openrouter',
      model: 'google/gemini-3.1-flash-image',
    });
    expect(activity.text).toContain('openrouter');
  });
});

describe('value helpers', () => {
  it('valueAsText converts JSON to pretty text by default', () => {
    expect(valueAsText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(valueAsText('plain')).toBe('plain');
    expect(valueAsText(null)).toBe('—');
  });
  it('isFileReference only matches workflow file references', () => {
    expect(isFileReference(fileRef())).toBe(true);
    expect(isFileReference({ kind: 'workflow_file' })).toBe(false);
    expect(isFileReference('text')).toBe(false);
  });
});

});
