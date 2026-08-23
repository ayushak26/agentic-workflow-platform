import { describe, expect, it } from 'vitest';

import { normalizeWorkflowStatus } from './workflowStatus';

describe('normalizeWorkflowStatus', () => {
  it('normalizes runtime and legacy presentation states to the five workflow states', () => {
    expect(normalizeWorkflowStatus('waiting')).toBe('pending');
    expect(normalizeWorkflowStatus('active')).toBe('running');
    expect(normalizeWorkflowStatus('reused')).toBe('done');
    expect(normalizeWorkflowStatus('needs_input')).toBe('paused');
    expect(normalizeWorkflowStatus('failed')).toBe('error');
    expect(normalizeWorkflowStatus('rejected')).toBe('error');
  });
});