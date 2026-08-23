import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../../api/client';
import type { PrivateChatWorkflowSummary, WorkflowSummary } from '../../../api/types';
import { AddWorkflowDialog } from './AddWorkflowDialog';

vi.mock('../../../api/client', () => ({
  api: {
    generatePrivateChatWorkflow: vi.fn(),
    importPrivateChatWorkflow: vi.fn(),
    copyPrivateChatWorkflow: vi.fn(),
    listWorkflows: vi.fn(),
  },
}));

const created: PrivateChatWorkflowSummary = {
  id: 'cwf_1', slug: 'research', name: 'Research', description: 'Private research',
  source: 'generated', visibility: 'private', status: 'private',
  output_compatibility: { supported: true, detected_types: ['text'], fallback_to_text: false, warnings: [] },
  created_at: '2026-01-01', updated_at: '2026-01-01',
};

describe('AddWorkflowDialog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('generates and saves a private workflow with the selected output preference', async () => {
    vi.mocked(api.generatePrivateChatWorkflow).mockResolvedValueOnce(created);
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<AddWorkflowDialog onClose={vi.fn()} onCreated={onCreated} />);

    await user.type(screen.getByLabelText('Display name'), 'Research');
    await user.type(screen.getByLabelText('What should this workflow do?'), 'Research a company.');
    await user.selectOptions(screen.getByLabelText('Preferred visible output'), 'pdf');
    await user.click(screen.getByRole('button', { name: 'Add privately' }));

    expect(api.generatePrivateChatWorkflow).toHaveBeenCalledWith({
      prompt: 'Research a company.', slug: 'research', display_name: 'Research',
      preferred_output_type: 'pdf',
    });
    expect(onCreated).toHaveBeenCalledWith(created);
  });

  it('imports YAML privately', async () => {
    vi.mocked(api.importPrivateChatWorkflow).mockResolvedValueOnce({ ...created, source: 'imported' });
    const user = userEvent.setup();
    render(<AddWorkflowDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Import YAML' }));
    await user.type(screen.getByLabelText('Display name'), 'Imported');
    await user.type(screen.getByLabelText('Workflow YAML'), 'name: imported');
    await user.click(screen.getByRole('button', { name: 'Add privately' }));
    expect(api.importPrivateChatWorkflow).toHaveBeenCalledWith({
      slug: 'imported', display_name: 'Imported', yaml: 'name: imported',
    });
  });

  it('copies an existing workflow into the private catalog', async () => {
    vi.mocked(api.listWorkflows).mockResolvedValueOnce([{
      name: 'shared_flow', description: 'Shared source', use_case: 'generic', version: '1',
      node_count: 1, updated_at: 'now', library: null,
      readiness: { level: 'ready', items: [] },
    } satisfies WorkflowSummary]);
    vi.mocked(api.copyPrivateChatWorkflow).mockResolvedValueOnce({ ...created, source: 'existing' });
    const user = userEvent.setup();
    render(<AddWorkflowDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Add existing' }));
    await user.type(screen.getByLabelText('Display name'), 'My shared flow');
    await user.click(await screen.findByRole('radio', { name: /shared_flow/i }));
    await user.click(screen.getByRole('button', { name: 'Add privately' }));
    expect(api.copyPrivateChatWorkflow).toHaveBeenCalledWith({
      workflow_name: 'shared_flow', slug: 'my_shared_flow', display_name: 'My shared flow',
    });
  });

  it('shows duplicate-name failures without closing', async () => {
    vi.mocked(api.generatePrivateChatWorkflow).mockRejectedValueOnce(new Error('409 already exists'));
    const user = userEvent.setup();
    render(<AddWorkflowDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByLabelText('Display name'), 'Research');
    await user.type(screen.getByLabelText('What should this workflow do?'), 'Research.');
    await user.click(screen.getByRole('button', { name: 'Add privately' }));
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});