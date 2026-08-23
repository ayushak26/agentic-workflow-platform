import { describe, expect, it } from 'vitest';

import { plainTextJsonAdapter } from './hitl-plain-text';

describe('plainTextJsonAdapter', () => {
  it('shows a JSON string as plain text and re-encodes edits', () => {
    const adapter = plainTextJsonAdapter('"Draft response"');

    expect(adapter.displayText).toBe('Draft response');
    expect(JSON.parse(adapter.serialize('Approved response'))).toBe('Approved response');
  });

  it('edits a preferred text field without discarding surrounding JSON', () => {
    const adapter = plainTextJsonAdapter(JSON.stringify({
      answer: 'Original answer',
      confidence: 0.92,
      sources: ['policy.pdf'],
    }));

    expect(adapter.displayText).toBe('Original answer');
    expect(JSON.parse(adapter.serialize('Corrected answer'))).toEqual({
      answer: 'Corrected answer',
      confidence: 0.92,
      sources: ['policy.pdf'],
    });
  });

  it('finds a preferred text field inside nested structured output', () => {
    const adapter = plainTextJsonAdapter(JSON.stringify({
      parsed: { draft: 'Customer-facing draft', category: 'support' },
      model: 'example-model',
    }));

    expect(adapter.displayText).toBe('Customer-facing draft');
    expect(JSON.parse(adapter.serialize('Human-edited draft'))).toEqual({
      parsed: { draft: 'Human-edited draft', category: 'support' },
      model: 'example-model',
    });
  });

  it('renders ambiguous structures readably and submits plain text as valid JSON', () => {
    const adapter = plainTextJsonAdapter(JSON.stringify({
      customer: 'Example GmbH',
      priority: 'high',
      score: 8,
    }));

    expect(adapter.displayText).toContain('customer: Example GmbH');
    expect(adapter.displayText).toContain('priority: high');
    expect(JSON.parse(adapter.serialize('Route this request to Service.')))
      .toBe('Route this request to Service.');
  });

  it('does not expose malformed JSON syntax to the reviewer', () => {
    const adapter = plainTextJsonAdapter('ordinary text from a legacy gate');

    expect(adapter.displayText).toBe('ordinary text from a legacy gate');
    expect(JSON.parse(adapter.serialize('edited text'))).toBe('edited text');
  });
});