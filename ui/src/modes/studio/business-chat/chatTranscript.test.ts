import { describe, expect, it } from 'vitest';

import type { BusinessChatTranscriptMessage } from '../../../api/types';
import { boundedConversationSummary, deserializeDurableMessage, normalizePersistedAssistantSegments, serializeDurableMessage } from './chatTranscript';

describe('Business Chat durable transcript mapping', () => {
  it('builds bounded grounded context and excludes ungrounded fallback answers', () => {
    const summary = boundedConversationSummary([
      { id: 'u1', role: 'user', text: 'Which pumps suit chemical transfer?' },
      { id: 'a1', role: 'assistant', runId: 'r1', segments: [{ kind: 'text', text: 'Mag-drive pumps avoid shaft seals. [1]' }] },
      { id: 'u2', role: 'user', text: 'What about maintenance?' },
      { id: 'a2', role: 'assistant', runId: 'r2', responseLabel: 'General answer · not grounded in selected Knowledge', segments: [{ kind: 'text', text: 'Generic unsupported answer.' }] },
    ]);
    expect(summary).toContain('User: Which pumps suit chemical transfer?');
    expect(summary).toContain('Assistant: Mag-drive pumps avoid shaft seals. [1]');
    expect(summary).toContain('User: What about maintenance?');
    expect(summary).not.toContain('Generic unsupported answer.');
  });
  it('round trips a user message with attachments', () => {
    const local = {
      id: 'message-1',
      role: 'user' as const,
      text: 'Analyze this',
      attachments: [{
        kind: 'workflow_file' as const,
        file_id: 'file-1',
        name: 'brief.pdf',
        content_type: 'application/pdf',
        size_bytes: 123,
        minio_key: 'inputs/brief.pdf',
        sha256: 'abc',
        extension: 'pdf',
        category: 'document' as const,
        parseable_text: true,
      }],
    };
    const serialized = serializeDurableMessage(local);
    const restored = deserializeDurableMessage({
      id: local.id,
      ...serialized,
      created_at: '2026-08-23T00:00:00Z',
      updated_at: '2026-08-23T00:00:00Z',
    });
    expect(restored).toEqual(local);
  });

  it('round trips an assistant answer with its downloadable structured result', () => {
    const local = {
      id: 'answer-1', role: 'assistant' as const,
      segments: [{ kind: 'text' as const, text: 'The request was processed.' }],
      runId: 'run-1', structuredResult: { source_file: 'Chat message', status: 'complete' },
      responseLabel: 'General answer · not grounded in selected Knowledge',
    };
    const serialized = serializeDurableMessage(local);
    expect(deserializeDurableMessage({
      id: local.id, ...serialized,
      created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
    })).toEqual(local);
  });

  it('rejects malformed persisted content instead of crashing the chat', () => {
    const malformed: BusinessChatTranscriptMessage = {
      id: 'bad', role: 'assistant', content: { segments: 'not-an-array' }, run_id: null,
      created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
    };
    expect(deserializeDurableMessage(malformed)).toBeNull();
  });

  it('restores older pending reviews that predate durable gate ids', () => {
    const restored = deserializeDurableMessage({
      id: 'old-review', role: 'intervention', run_id: 'run-1',
      content: { status: 'pending', request: { runId: 'run-1', parentRunId: null, nodeId: 'review', question: 'Approve?', reviewPurpose: '', context: {}, allowedActions: ['approve'], content: null, panels: [], allowDocumentOverride: false, maxEditChars: 100, displayName: 'Review' } },
      created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
    });
    expect(restored?.role === 'intervention' ? restored.request.gateId : null).toBe('run-1:review');
  });

  it('repairs persisted technical envelopes into the embedded final answer', () => {
    expect(normalizePersistedAssistantSegments([
      { kind: 'text', text: 'Start\nData: [structured value]\nMessage: Explain React JS' },
      { kind: 'text', text: 'Answer\nRaw: {"answer":"React is a UI library."}\nParsed: [structured value]\nStatus: ok' },
      { kind: 'text', text: 'Reply\nResult: [structured value]' },
    ])).toEqual([{ kind: 'text', text: 'React is a UI library.' }]);
  });

  it('removes persisted file-loader internals after recovering the final answer', () => {
    expect(normalizePersistedAssistantSegments([
      { kind: 'text', text: 'Start\nData: [structured value]\nMessage: Explain Eurskem AI' },
      { kind: 'text', text: 'Load Files\nText: EURSKEM AI · ENGINEERING DOCUMENTATION\nFiles: 1 item\nText File Count: 1' },
      { kind: 'text', text: 'Answer\nRaw: {"answer":"Eurskem AI uses four architecture layers."}\nParsed: [structured value]\nStatus: ok' },
      { kind: 'text', text: 'Reply\nResult: [structured value]' },
    ])).toEqual([{ kind: 'text', text: 'Eurskem AI uses four architecture layers.' }]);
  });

  it('repairs persisted RAG diagnostics into the no-evidence answer', () => {
    expect(normalizePersistedAssistantSegments([
      { kind: 'text', text: 'Start\nData: [structured value]\nMessage: Give examples of different pumps in different industries\nAttachments: 0 items\nMissing: 0 items' },
      { kind: 'text', text: 'Rag\nQuery: Give examples of different pumps in different industries\nAnswer: No supporting information was found in the selected knowledge collection.\nCitations: 0 items\nSources: 0 items\nRelevant Context: 0 items\nRetrievals: 0 items\nGrounding For Drafter: [structured value]\nRetrieval Trace Id: retreq-1\nCollection Id: col-1\nResolved Index Id: idx-1' },
      { kind: 'text', text: 'Reply\nResult: [structured value]' },
    ])).toEqual([{ kind: 'text', text: 'No supporting information was found in the selected knowledge collection.' }]);
  });
});