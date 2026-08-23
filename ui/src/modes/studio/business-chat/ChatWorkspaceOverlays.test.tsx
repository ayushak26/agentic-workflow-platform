import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { knowledgeApi } from '../../../api/knowledge';
import { api } from '../../../api/client';
import { ActivityDrawer, ChatHistoryDrawer, CitationDrawer, ExistingWorkflowDrawer, SourcePickerDialog } from './ChatWorkspaceOverlays';

vi.mock('../../../api/client', () => ({ api: {
  integrationConnections: vi.fn(), integrationConnectUrl: vi.fn(() => '/api/builder/integrations/connect/google_drive'),
  browseIntegrationFiles: vi.fn(), downloadIntegrationFileUrl: vi.fn(),
} }));

vi.mock('../../../api/knowledge', () => ({
  knowledgeApi: {
    documentSourceUrl: vi.fn(),
    getTraceChunkContext: vi.fn(),
    listCollections: vi.fn(),
    listDocuments: vi.fn(),
  },
}));

describe('Chat workspace drawers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(knowledgeApi.documentSourceUrl).mockRejectedValue(new Error('No source URL in this test'));
    vi.mocked(api.integrationConnections).mockResolvedValue({ connections: [], configured: false });
  });

  it('adds entered URLs as explicit sources', async () => {
    const onAddUrls = vi.fn();
    render(<SourcePickerDialog open sourceCount={0} onClose={vi.fn()} onUpload={vi.fn()} onAddUrls={onAddUrls} onImportDrive={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Website or URL/ }));
    fireEvent.change(screen.getByPlaceholderText(/https:\/\/example.com\/report/), { target: { value: 'https://example.com/report' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add URLs' }));
    expect(onAddUrls).toHaveBeenCalledWith('https://example.com/report');
  });

  it('shows connected Google Drive accounts and imports selected files', async () => {
    const connection = { id: 'drive-1', provider: 'google_drive' as const, display_name: 'Work Drive', address: 'me@example.com', needs_reauth: false };
    vi.mocked(api.integrationConnections).mockResolvedValue({ connections: [connection], configured: true });
    vi.mocked(api.browseIntegrationFiles).mockResolvedValue({ files: [{ id: 'file-1', name: 'Board pack.pdf', is_folder: false, mime_type: 'application/pdf' }] });
    const onImportDrive = vi.fn();
    render(<SourcePickerDialog open sourceCount={2} onClose={vi.fn()} onUpload={vi.fn()} onAddUrls={vi.fn()} onImportDrive={onImportDrive} />);
    fireEvent.click(screen.getByRole('button', { name: /Google Drive/ }));
    expect(await screen.findByRole('option', { name: /Work Drive/ })).toBeVisible();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select Board pack.pdf' }));
    fireEvent.click(screen.getByRole('button', { name: /Add 1 file/ }));
    await waitFor(() => expect(onImportDrive).toHaveBeenCalledWith(connection, [expect.objectContaining({ id: 'file-1', name: 'Board pack.pdf', mimeType: 'application/pdf' })]));
  });
  it('closes on Escape and restores focus to the trigger', () => {
    const onClose = vi.fn();
    const { rerender } = render(<><button type="button">Activity trigger</button><ActivityDrawer open={false} activities={[]} runId={null} onClose={onClose} /></>);
    const trigger = screen.getByRole('button', { name: 'Activity trigger' });
    trigger.focus();
    rerender(<><button type="button">Activity trigger</button><ActivityDrawer open activities={[]} runId={null} onClose={onClose} /></>);
    const dialog = screen.getByRole('dialog', { name: 'Activity' });
    expect(screen.getByRole('button', { name: 'Close Activity' })).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
    rerender(<><button type="button">Activity trigger</button><ActivityDrawer open={false} activities={[]} runId={null} onClose={onClose} /></>);
    expect(screen.getByRole('button', { name: 'Activity trigger' })).toHaveFocus();
  });

  it('cycles focus within the drawer', () => {
    render(<ActivityDrawer open activities={[]} runId="run-1" onClose={vi.fn()} />);
    const close = screen.getByRole('button', { name: 'Close Activity' });
    const technical = screen.getByRole('link', { name: 'Open technical execution' });
    technical.focus();
    fireEvent.keyDown(technical, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(technical).toHaveFocus();
  });

  it('labels browser-local history and exposes chat management actions', () => {
    const chat = { id: 'chat-1', title: 'Customer interviews', createdAt: '2026-08-23T00:00:00Z', updatedAt: '2026-08-23T00:00:00Z', workflowId: null, workflowSource: null, conversationId: null, runId: null } as const;
    const onOpen = vi.fn();
    render(<ChatHistoryDrawer open chats={[chat]} activeChatId={chat.id} onClose={vi.fn()} onNew={vi.fn()} onOpen={onOpen} onRename={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText(/Stored only in this browser/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Customer interviews').closest('button') as HTMLButtonElement);
    expect(onOpen).toHaveBeenCalledWith(chat);
    expect(screen.getByRole('button', { name: 'Rename Customer interviews' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Delete Customer interviews' })).toBeVisible();
  });

  it('searches and opens an existing workflow without exposing management actions', () => {
    const onOpen = vi.fn();
    render(<ExistingWorkflowDrawer open loading={false} error={null} workflows={[
      { id: 'private-1', source: 'private', title: 'Pump ICP2', description: 'Summarize pump documentation.', status: 'private' },
      { id: 'shared-1', source: 'shared', title: 'Customer triage', description: 'Route customer requests.', status: 'approved' },
    ]} onClose={vi.fn()} onOpen={onOpen} />);
    fireEvent.change(screen.getByPlaceholderText('Search by name or purpose…'), { target: { value: 'pump' } });
    expect(screen.getByText('Pump ICP2')).toBeVisible();
    expect(screen.queryByText('Customer triage')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Pump ICP2').closest('button') as HTMLButtonElement);
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'private-1', source: 'private' }));
    expect(screen.queryByRole('button', { name: /archive|publish/i })).not.toBeInTheDocument();
  });

  it('shows blocked Builder workflows but prevents opening them', () => {
    const onOpen = vi.fn();
    render(<ExistingWorkflowDrawer open loading={false} error={null} workflows={[
      { id: 'builder-broken', source: 'shared', title: 'Builder draft', description: '', status: 'blocked' },
    ]} onClose={vi.fn()} onOpen={onOpen} />);
    const workflow = screen.getByRole('button', { name: /Builder draft/ });
    expect(workflow).toBeDisabled();
    expect(screen.getByText(/fix readiness errors in Builder/)).toBeVisible();
    fireEvent.click(workflow);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('loads surrounding context lazily for a cited retrieval chunk', async () => {
    vi.mocked(knowledgeApi.getTraceChunkContext).mockResolvedValue({
      retrieval_request_id: 'trace-1',
      previous: { chunk_id: 'a', document_id: 'doc-1', title: 'Manual.pdf', text: 'Previous passage', page: 3, section: null },
      current: { chunk_id: 'b', document_id: 'doc-1', title: 'Manual.pdf', text: 'Cited passage', page: 4, section: 'Maintenance' },
      next: { chunk_id: 'c', document_id: 'doc-1', title: 'Manual.pdf', text: 'Following passage', page: 5, section: null },
    });
    render(<CitationDrawer citation={{
      number: 1, title: 'Manual.pdf', snippet: 'Cited passage', documentId: 'doc-1', chunkId: 'b',
      retrievalTraceId: 'trace-1', evidenceStatus: 'retrieved_not_verified', sourceType: 'internal_document',
    }} onClose={vi.fn()} onSave={vi.fn()} onAsk={vi.fn()} />);

    expect(knowledgeApi.getTraceChunkContext).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Show surrounding context' }));
    expect(await screen.findByText('Previous passage', { selector: 'p' })).toBeVisible();
    expect(screen.getByText('Following passage', { selector: 'p' })).toBeVisible();
    expect(knowledgeApi.getTraceChunkContext).toHaveBeenCalledWith('trace-1', 'b');
  });
});