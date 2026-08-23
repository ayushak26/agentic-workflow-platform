import { describe, expect, it } from 'vitest';

import type { BusinessChatTranscriptMessage } from '../../../api/types';
import { deserializeDurableMessage, serializeDurableMessage } from './chatTranscript';

describe('Business Chat durable transcript mapping', () => {
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

  it('rejects malformed persisted content instead of crashing the chat', () => {
    const malformed: BusinessChatTranscriptMessage = {
      id: 'bad', role: 'assistant', content: { segments: 'not-an-array' }, run_id: null,
      created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
    };
    expect(deserializeDurableMessage(malformed)).toBeNull();
  });
});