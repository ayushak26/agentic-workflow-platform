import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';
import yaml from 'js-yaml';

import type { RunEvent, WorkflowFileReference } from '../../../api/types';
import {
  assistantSegments,
  activityFromNodeRun,
  businessActivityLabel,
  buildRunInputs,
  chatMetaFromYaml,
  compatibleTransformModels,
  composerDisabledReason,
  eventProgressLabel,
  GENERIC_CHAT_EXPERIENCES,
  interventionFromPendingGate,
  structuredResultFromRun,
  isFileReference,
  resolveComposerIntent,
  valueAsText,
  withTransformModel,
  type WorkflowChatMeta,
} from './businessChatModel';

describe('generic Chat experiences', () => {
  it('offers only the four business-level choices', () => {
    expect(GENERIC_CHAT_EXPERIENCES.map(item => item.title)).toEqual([
      'General', 'Analyze sources', 'Research', 'Create',
    ]);
  });
});

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
  it('passes only optional inputs declared by the workflow', () => {
    expect(buildRunInputs(
      { ...chatbotMeta, declaredInputs: ['conversation_summary'] },
      'What about maintenance?', {}, [],
      { conversation_summary: 'User: Which pumps suit chemical transfer?', collection_id: 'forbidden' },
    )).toEqual({
      message: 'What about maintenance?',
      conversation_summary: 'User: Which pumps suit chemical transfer?',
    });
  });
  it('passes selected web-page constraints only when the workflow declares them', () => {
    expect(buildRunInputs(
      { ...chatbotMeta, declaredInputs: ['web_source_urls'] },
      'Compare these sources', {}, [],
      { web_source_urls: ['https://example.com/policy'], collection_id: 'forbidden' },
    )).toEqual({
      message: 'Compare these sources',
      web_source_urls: ['https://example.com/policy'],
    });
  });
  it('maps form fields by name for input_form Starts', () => {
    const meta = chatMetaFromYaml(
      readFileSync(resolve(REFERENCE_DIR, 'ref_knowledge_triage.yaml'), 'utf8'),
    );
    expect(buildRunInputs(meta, '', { request: 'Need a refund' }))
      .toEqual({ request: 'Need a refund' });
  });
});

describe('structuredResultFromRun', () => {
  it('extracts the original workflow result from a Chat adapter handoff', () => {
    const result = { source_file: 'Chat message', processed_at: '2026-08-23T22:00:00Z', extraction: { customer_name: 'Ada' } };
    expect(structuredResultFromRun({
      outputs: { outcome: 'answered', message: 'The order request was processed.', handoff: { structured_result: result } },
    })).toEqual(result);
  });

  it('returns null for ordinary chat responses without a structured handoff', () => {
    expect(structuredResultFromRun({ outputs: { outcome: 'answered', message: 'Hello' } })).toBeNull();
  });
});

describe('resolveComposerIntent', () => {
  it('first message runs the workflow; follow-ups ask AI', () => {
    expect(resolveComposerIntent(false, 'auto')).toBe('run');
    expect(resolveComposerIntent(true, 'auto')).toBe('ask');
  });
  it('runs General Chat again for every turn', () => {
    expect(resolveComposerIntent(true, 'auto', true)).toBe('run');
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
      gate_id: 'gate-1',
      paused: true,
      pause_kind: 'hitl_gate',
      node_id: 'review',
      parent_run_id: 'parent-1',
      question: 'Approve the drafted answer?',
      review_purpose: 'Customer-facing answers need a human check.',
      context: { 'draft.parsed.draft': 'Hello…' },
      allowed_actions: ['approve', 'edit', 'reject'],
      content: { text: 'Hello…', format: 'text', source: 'workflow' },
      panels: [{
        label: 'Draft', field: 'draft.text', hint: 'Customer-facing copy',
        editable: false, value: 'Hello…', available: true,
      }],
      allow_document_override: false,
      max_edit_chars: 5000,
    }, meta);
    expect(request).not.toBeNull();
    expect(request?.runId).toBe('run-1');
    expect(request?.gateId).toBe('gate-1');
    expect(request?.parentRunId).toBe('parent-1');
    expect(request?.nodeId).toBe('review');
    expect(request?.question).toBe('Approve the drafted answer?');
    expect(request?.allowedActions).toEqual(['approve', 'edit', 'reject']);
    expect(request?.allowDocumentOverride).toBe(false);
    expect(request?.maxEditChars).toBe(5000);
    expect(request?.displayName).toBe('Human Review');
    expect(request?.panels[0].label).toBe('Draft');
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
  it('is null for internal subprocess waits', () => {
    expect(interventionFromPendingGate(
      { run_id: 'r', paused: true, pause_kind: 'subprocess', node_id: 'run_workflow' },
      meta,
    )).toBeNull();
    expect(interventionFromPendingGate({
      run_id: 'r', paused: true, pause_kind: 'hitl_gate',
      interrupt: { kind: 'subprocess_pause', node_id: 'run_workflow', context: {} },
    }, meta)).toBeNull();
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
  it('renders the final chat answer with supported artifacts and citations', () => {
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
    expect(kinds).toEqual(['text', 'image', 'sources']);
    expect(segments[0]).toEqual({ kind: 'text', text: 'Contoso has expanded…' });
    expect(segments[1]).toEqual(expect.objectContaining({ kind: 'image', title: 'chart.png' }));
    expect(segments[2]).toEqual({ kind: 'sources', items: [{
      number: 1, title: 'Handbook.pdf', documentId: 'd1', sourceType: 'internal_document',
    }] });
  });

  it('never fabricates sources without a KnowledgeRetrieval node', () => {
    const segments = assistantSegments({
      outputs: { answer: 'hi' },
      node_runs: { e: { output: { chat_message: 'hi' } } },
      node_types: { e: 'EndAgent' },
    });
    expect(segments.some(s => s.kind === 'sources')).toBe(false);
  });

  it('renders a RAGAgent answer without exposing technical fields when retrieval is empty', () => {
    const segments = assistantSegments({
      outputs: { rag: { answer: 'No supporting information was found in the selected knowledge collection.', citations: [], retrievals: [] } },
      node_types: { rag: 'RAGAgent' },
      node_runs: { rag: { output: {
        answer: 'No supporting information was found in the selected knowledge collection.',
        citations: [], retrievals: [], retrieval_trace_id: 'retreq-1', collection_id: 'col-1', resolved_index_id: 'idx-1',
      } } },
    });
    expect(segments).toEqual([{ kind: 'text', text: 'No supporting information was found in the selected knowledge collection.' }]);
  });

  it('projects real web results as citation cards with snippets and safe links', () => {
    const segments = assistantSegments({
      outputs: { answer: 'Current information [1][2]' },
      node_types: { web: 'WebSearchAgent' },
      node_runs: { web: { output: { results: [
        { title: 'Official update', url: 'https://example.com/update', snippet: 'The update was published today.', status: 'candidate_only' },
        { title: 'Unsafe result', url: 'javascript:alert(1)', snippet: 'Returned by the provider.', status: 'candidate_only' },
      ] } } },
    });
    expect(segments.at(-1)).toEqual({ kind: 'sources', items: [
      { number: 1, title: 'Official update', snippet: 'The update was published today.', sourceUri: 'https://example.com/update', evidenceStatus: 'candidate_only', sourceType: 'webpage' },
      { number: 2, title: 'Unsafe result', snippet: 'Returned by the provider.', evidenceStatus: 'candidate_only', sourceType: 'webpage' },
    ] });
  });

  it('deduplicates repeated web URLs and numbers mixed sources sequentially', () => {
    const segments = assistantSegments({
      node_types: { web: 'WebSearchAgent', knowledge: 'KnowledgeRetrieval' },
      node_runs: {
        web: { output: { results: [
          { title: 'First title', url: 'https://example.com/a', snippet: 'First' },
          { title: 'Duplicate title', url: 'https://example.com/a', snippet: 'Duplicate' },
        ] } },
        knowledge: { output: {
          citations: [{ filename: 'Handbook.pdf', document_id: 'doc-1', chunk_id: 'chunk-1' }],
          retrieved_chunks: [{ chunk_id: 'chunk-1', display_number: 42, text: 'Internal passage' }],
        } },
      },
    });
    const sources = segments.at(-1);
    expect(sources).toEqual({ kind: 'sources', items: [
      { number: 1, title: 'First title', snippet: 'First', sourceUri: 'https://example.com/a', evidenceStatus: 'candidate_only', sourceType: 'webpage' },
      { number: 2, title: 'Handbook.pdf', snippet: 'Internal passage', documentId: 'doc-1', chunkId: 'chunk-1', sourceType: 'internal_document' },
    ] });
  });

  it('projects Deep Research web and paper candidates and enriches acquired PDFs', () => {
    const segments = assistantSegments({
      run_id: 'run research/1',
      node_types: { research: 'BoundedDeepResearchAgent', acquire: 'ResearchSourceAcquirer' },
      node_runs: {
        research: { output: {
          dossiers: [{ citations: [
            { url: 'https://doi.org/10.1000/paper', cited_text: 'The study reported a significant effect.' },
            { url: 'https://agency.example/policy', cited_text: 'The agency published updated guidance.' },
          ] }],
          candidates: [
            { candidate_id: 'CAND-PAPER', title: 'Peer-reviewed study', canonical_url: 'https://doi.org/10.1000/paper', doi: '10.1000/paper', source: 'crossref' },
            { candidate_id: 'CAND-WEB', title: 'Agency guidance', canonical_url: 'https://agency.example/policy', source: 'web' },
          ],
        } },
        acquire: { output: { documents: [{
          document_id: 'DOC/PAPER', candidate_id: 'CAND-PAPER',
          pdf_object_key: 'evidence/run/paper.pdf',
        }] } },
      },
    });
    expect(segments.at(-1)).toEqual({ kind: 'sources', items: [
      {
        number: 1, title: 'Peer-reviewed study',
        snippet: 'The study reported a significant effect.',
        sourceUri: 'https://doi.org/10.1000/paper', documentId: 'DOC/PAPER',
        downloadUrl: '/api/candidates/run%20research%2F1/documents/DOC%2FPAPER/download',
        evidenceStatus: 'acquired_full_text', sourceType: 'research_paper',
      },
      {
        number: 2, title: 'Agency guidance',
        snippet: 'The agency published updated guidance.',
        sourceUri: 'https://agency.example/policy', evidenceStatus: 'candidate_only',
        sourceType: 'webpage',
      },
    ] });
  });

  it('projects added files, paper-search-mcp papers, web, and RAG sources in stable lane order', () => {
    const segments = assistantSegments({
      node_types: {
        knowledge: 'RAGAgent', research: 'BoundedDeepResearchAgent', papers: 'MCPToolAgent',
        web: 'WebSearchAgent', files: 'WorkflowFileLoader',
      },
      node_runs: {
        knowledge: { output: {
          citations: [{ filename: 'Internal handbook.pdf', document_id: 'internal-1', chunk_id: 'chunk-1' }],
          retrievals: [{ chunk_id: 'chunk-1', text: 'Internal policy passage.' }],
        } },
        research: { output: { candidates: [{
          candidate_id: 'candidate-1', title: 'Official guidance', canonical_url: 'https://agency.example/guidance', source: 'web',
        }] } },
        papers: { output: {
          server: 'paper-search-mcp', tool: 'search_papers',
          data: { papers: [{ title: 'Scholarly result', doi: '10.1000/example', abstract: 'Peer-reviewed abstract.' }] },
        } },
        web: { output: { results: [{ title: 'Current web result', url: 'https://example.com/current', snippet: 'Current web evidence.' }] } },
        files: { output: { files: [{ name: 'Drive source.docx', status: 'extracted' }] } },
      },
    });

    expect(segments.at(-1)).toEqual({ kind: 'sources', items: [
      { number: 1, title: 'Drive source.docx', evidenceStatus: 'extracted', sourceType: 'internal_document' },
      { number: 2, title: 'Current web result', snippet: 'Current web evidence.', sourceUri: 'https://example.com/current', evidenceStatus: 'candidate_only', sourceType: 'webpage' },
      { number: 3, title: 'Scholarly result', snippet: 'Peer-reviewed abstract.', sourceUri: 'https://doi.org/10.1000/example', evidenceStatus: 'candidate_only', sourceType: 'research_paper' },
      { number: 4, title: 'Official guidance', sourceUri: 'https://agency.example/guidance', evidenceStatus: 'candidate_only', sourceType: 'webpage' },
      { number: 5, title: 'Internal handbook.pdf', snippet: 'Internal policy passage.', documentId: 'internal-1', chunkId: 'chunk-1', sourceType: 'internal_document' },
    ] });
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

  it('hides runtime plumbing and gives generic transforms a business label', () => {
    expect(businessActivityLabel(workflowMeta.nodes[0])).toBeNull();
    expect(businessActivityLabel({
      ...workflowMeta.nodes[1], type: 'TransformAgent', displayName: 'Prepare Answer',
    })).toBe('Prepared response');
    expect(businessActivityLabel({
      ...workflowMeta.nodes[1], type: 'EndAgent', displayName: 'Chat Reply',
    })).toBeNull();
    const prepared = activityFromNodeRun({
      ...workflowMeta.nodes[1], type: 'TransformAgent', displayName: 'Prepare Answer',
    }, {
      node_id: 'research', type_name: 'TransformAgent', status: 'completed', input: {}, output: {},
      started_at: 1, ended_at: 2, duration_s: 1, error: null,
    }, workflowMeta);
    expect(prepared.displayName).toBe('Prepared response');
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
    expect(activity.displayName).toBe('Searched the web');
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

  it('shows a private conversation-aware retrieval rewrite in Activity', () => {
    const rewriteNode = {
      ...workflowMeta.nodes[1], id: 'rewrite_query', type: 'TransformAgent', displayName: 'Prepare Retrieval Query',
    };
    const activity = activityFromNodeRun(rewriteNode, {
      node_id: 'rewrite_query', type_name: 'TransformAgent', status: 'completed', input: {},
      output: { parsed: { retrieval_query: 'Maintenance requirements for the industrial pumps discussed previously' } },
      started_at: 1, ended_at: 2, duration_s: 1, error: null,
    }, workflowMeta);
    expect(activity.text).toBe('Retrieval query: Maintenance requirements for the industrial pumps discussed previously');
    expect(activity.tool?.label).toBe('Knowledge query rewrite');
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

describe('rich source citations', () => {
  it('projects exact retrieved passages and real provenance without fabricating links', () => {
    const segments = assistantSegments({
      outputs: { answer: 'Grounded answer [1]' },
      node_types: { retrieve: 'KnowledgeRetrieval' },
      node_runs: { retrieve: { output: {
        citations: [{ document_id: 'doc-1', chunk_id: 'chunk-1', filename: 'Policy.pdf', page: 4, section: 'Scope', evidence_status: 'retrieved_not_verified' }],
        retrieved_chunks: [{ chunk_id: 'chunk-1', display_number: 7, compressed_text: 'The exact passage used by generation.', metadata: { source_uri: 'https://example.com/policy.pdf' } }],
        retrieval_trace_id: 'trace-1',
      } } },
    });
    expect(segments.at(-1)).toEqual({ kind: 'sources', items: [{
      number: 1, title: 'Policy.pdf', snippet: 'The exact passage used by generation.',
      page: 4, section: 'Scope', sourceUri: 'https://example.com/policy.pdf',
      documentId: 'doc-1', chunkId: 'chunk-1', retrievalTraceId: 'trace-1', evidenceStatus: 'retrieved_not_verified', sourceType: 'internal_document',
    }] });
  });
});

});
