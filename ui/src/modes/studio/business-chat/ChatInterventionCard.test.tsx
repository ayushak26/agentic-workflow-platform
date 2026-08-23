import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../../api/client';
import { ChatInterventionCard } from './ChatInterventionCard';
import type { DurableChatMessage } from './chatTranscript';

vi.mock('../../../api/client', () => ({ api: {
  workflowFileCapabilities: vi.fn(), uploadWorkflowFiles: vi.fn(),
  extractWorkflowFile: vi.fn(), resumeWorkflow: vi.fn(),
} }));

const message: Extract<DurableChatMessage, { role: 'intervention' }> = {
  id: 'intervention-gate-1', role: 'intervention', status: 'pending',
  request: {
    gateId: 'gate-1', runId: 'child-run', parentRunId: 'parent-run', nodeId: 'review',
    question: 'Approve the customer response?', reviewPurpose: 'A person must verify external communication.',
    context: { customer: 'Example GmbH' }, allowedActions: ['approve', 'edit', 'reject'],
    content: { text: JSON.stringify({ answer: 'Original answer', confidence: 0.9 }), format: 'json', source: 'workflow' },
    panels: [{ label: 'Customer', field: 'customer', value: 'Example GmbH', available: true }],
    allowDocumentOverride: true, maxEditChars: 5000, displayName: 'Customer Response Review',
  },
};

describe('ChatInterventionCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.workflowFileCapabilities).mockResolvedValue({
      categories: {}, extensions: ['.txt'], extractable_extensions: ['.txt'],
      reference_only_extensions: [], max_file_size_bytes: 1000, max_files_per_input: 1,
    });
    vi.mocked(api.resumeWorkflow).mockResolvedValue({ run_id: 'child-run', status: 'completed' });
  });

  it('approves the actionable child run and reports the decision after success', async () => {
    const user = userEvent.setup(); const onResult = vi.fn();
    render(<ChatInterventionCard message={message} onResult={onResult} />);
    expect(screen.getByText('Action required — Customer Response Review')).toBeVisible();
    expect(screen.getByText('Example GmbH')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Approve and continue' }));
    expect(api.resumeWorkflow).toHaveBeenCalledWith('child-run', { decision: 'approve' });
    expect(onResult).toHaveBeenCalledWith({ run_id: 'child-run', status: 'completed' }, 'approve');
  });

  it('edits JSON as ordinary text while preserving surrounding structured fields', async () => {
    const user = userEvent.setup(); const onResult = vi.fn();
    render(<ChatInterventionCard message={message} onResult={onResult} />);
    const editor = screen.getByLabelText('Edit before continuing');
    await user.clear(editor); await user.type(editor, 'Revised answer');
    await user.click(screen.getByRole('button', { name: 'Save changes and continue' }));
    expect(api.resumeWorkflow).toHaveBeenCalledWith('child-run', {
      decision: 'edit',
      edited_content: expect.objectContaining({
        text: JSON.stringify({ answer: 'Revised answer', confidence: 0.9 }),
        format: 'json', source: 'editor', source_document: null,
      }),
    });
    expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ status: 'completed' }), 'edit');
  });

  it('supports document replacement and submits the uploaded source reference', async () => {
    const user = userEvent.setup(); const onResult = vi.fn();
    const ref = { kind: 'workflow_file' as const, file_id: 'f1', name: 'replacement.txt', extension: 'txt', category: 'document', content_type: 'text/plain', size_bytes: 20, sha256: 'abc', minio_key: 'inputs/f1', parseable_text: true };
    vi.mocked(api.uploadWorkflowFiles).mockResolvedValue({ files: [ref] });
    vi.mocked(api.extractWorkflowFile).mockResolvedValue({ file: ref, text: 'Replacement answer', total_chars: 18, extracted_chars: 18, truncated: false });
    render(<ChatInterventionCard message={message} onResult={onResult} />);
    await user.upload(screen.getByLabelText('Replace with document'), new File(['Replacement answer'], 'replacement.txt', { type: 'text/plain' }));
    await screen.findByDisplayValue('Replacement answer');
    await user.click(screen.getByRole('button', { name: 'Save changes and continue' }));
    expect(api.resumeWorkflow).toHaveBeenCalledWith('child-run', expect.objectContaining({
      decision: 'edit', edited_content: expect.objectContaining({ source: 'upload', source_document: ref }),
    }));
  });

  it('keeps the review actionable and shows the error when resume fails', async () => {
    const user = userEvent.setup(); const onResult = vi.fn();
    vi.mocked(api.resumeWorkflow).mockRejectedValueOnce(new Error('The review was already resolved elsewhere.'));
    render(<ChatInterventionCard message={message} onResult={onResult} />);
    await user.type(screen.getByPlaceholderText('Optional rejection reason'), 'Incorrect customer');
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already resolved elsewhere');
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(onResult).not.toHaveBeenCalled();
  });
});