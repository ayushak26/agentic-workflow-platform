import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { KnowledgeRoot } from './KnowledgeRoot';
import { knowledgeApi, type CollectionResource } from '../../api/knowledge';

vi.mock('../../api/knowledge', () => ({
  knowledgeApi: {
    listCollections: vi.fn(),
    createCollection: vi.fn(),
    listDocuments: vi.fn(),
    listIndexes: vi.fn(),
    listProfiles: vi.fn(),
    listRagAgents: vi.fn(),
    retrievalPresets: vi.fn(),
    embeddingModels: vi.fn(),
    docTypes: vi.fn(),
    llmModels: vi.fn(),
    defaults: vi.fn(),
    traces: vi.fn(),
  },
}));

const api = vi.mocked(knowledgeApi);

function collection(overrides: Partial<CollectionResource> = {}): CollectionResource {
  return {
    collection_id: 'col_01TEST',
    name: 'Pump Manuals',
    description: '',
    status: 'draft',
    document_count: 0,
    chunk_count: 0,
    active_index_id: null,
    metadata_schema: {},
    doc_types: ['general'],
    workspace_id: 'ws_1',
    owner_scope_id: 'ayush',
    created_at: '2026-08-15T00:00:00Z',
    updated_at: '2026-08-15T00:00:00Z',
    ...overrides,
  } as CollectionResource;
}

beforeEach(() => {
  window.localStorage.clear();
  api.listCollections.mockResolvedValue([]);
  api.listProfiles.mockResolvedValue([]);
  api.listRagAgents.mockResolvedValue([]);
  api.retrievalPresets.mockResolvedValue({});
  api.embeddingModels.mockResolvedValue({ models: [], configured_default: '', endpoint: '' });
  api.docTypes.mockResolvedValue({ doc_types: [
    { id: 'general', label: 'General', description: 'Mixed prose.', precision_sensitive: false },
    { id: 'manual', label: 'Manual', description: 'Service manuals.', precision_sensitive: true },
  ] });
  api.llmModels.mockResolvedValue({ models: [] });
});

afterEach(() => vi.clearAllMocks());

describe('Knowledge Studio — creating the first collection', () => {
  it('creates the collection and shows it in the list', async () => {
    const created = collection();
    api.createCollection.mockResolvedValue(created);
    // First load is empty; after creating, the list contains the new collection.
    api.listCollections.mockResolvedValueOnce([]).mockResolvedValue([created]);

    render(<KnowledgeRoot />);
    await screen.findByRole('heading', { name: 'Create Collection' });

    await userEvent.type(
      screen.getByPlaceholderText('Dura 25 Product Knowledge'),
      'Pump Manuals',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Create collection' }));

    await waitFor(() => expect(api.createCollection).toHaveBeenCalledTimes(1));
    expect(api.createCollection).toHaveBeenCalledWith({
      name: 'Pump Manuals',
      description: '',
      doc_types: ['general'],
    });
    // The new collection must actually appear in the page's own list — a
    // create that silently leaves the page empty is the "button does nothing"
    // report. (The bar's <option> also carries the name, hence the scope.)
    const list = await screen.findByRole('heading', { name: 'Collections' });
    const section = list.closest('section') as HTMLElement;
    expect(within(section).getByText('Pump Manuals')).toBeTruthy();
  });

  it('sends the document types picked, not a free-text guess', async () => {
    const created = collection({ doc_types: ['general', 'manual'] });
    api.createCollection.mockResolvedValue(created);

    render(<KnowledgeRoot />);
    await screen.findByRole('heading', { name: 'Create Collection' });
    await userEvent.type(screen.getByPlaceholderText('Dura 25 Product Knowledge'), 'Manuals');

    // 'general' is preselected; adding 'Manual' makes the collection
    // precision-sensitive, which is what Auto embedding selection reads.
    await userEvent.click(await screen.findByRole('button', { name: /Manual/ }));
    expect(await screen.findByText(/precision-sensitive/)).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Create collection' }));
    await waitFor(() => expect(api.createCollection).toHaveBeenCalledWith(
      expect.objectContaining({ doc_types: ['general', 'manual'] }),
    ));
  });

  it('surfaces a failure instead of silently doing nothing', async () => {
    api.createCollection.mockRejectedValue(new Error('409 name already exists'));

    render(<KnowledgeRoot />);
    await screen.findByRole('heading', { name: 'Create Collection' });

    await userEvent.type(
      screen.getByPlaceholderText('Dura 25 Product Knowledge'),
      'Duplicate',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Create collection' }));

    expect(await screen.findByText(/name already exists/)).toBeTruthy();
  });

  it('shows no dead "create the first collection" button on the Collections tab', async () => {
    render(<KnowledgeRoot />);
    await screen.findByRole('heading', { name: 'Create Collection' });
    // The Collections tab IS the create form. An empty-state button here would
    // only switch to the tab already open — a button that does nothing.
    expect(screen.queryByRole('button', { name: 'Create the first collection' })).toBeNull();
  });

  it('offers the empty-state route to the form from another tab', async () => {
    render(<KnowledgeRoot />);
    await screen.findByRole('heading', { name: 'Create Collection' });
    await userEvent.click(screen.getByRole('button', { name: 'Ingestion' }));

    const cta = await screen.findByRole('button', { name: 'Create the first collection' });
    await userEvent.click(cta);
    expect(await screen.findByPlaceholderText('Dura 25 Product Knowledge')).toBeTruthy();
  });
});
