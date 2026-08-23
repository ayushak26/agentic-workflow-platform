import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { knowledgeApi } from '../../../api/knowledge';
import { ResourceSelect } from './ResourceSelect';

vi.mock('../../../api/knowledge', () => ({
  knowledgeApi: { listCollections: vi.fn(), listProfiles: vi.fn() },
}));

describe('ResourceSelect', () => {
  it('shows authorized Knowledge Studio collections and disables unready ones', async () => {
    vi.mocked(knowledgeApi.listCollections).mockResolvedValueOnce([
      { collection_id: 'ready', name: 'Policies', status: 'ready', document_count: 2, chunk_count: 8, active_index_id: 'idx', description: '', metadata_schema: {}, doc_types: [] },
      { collection_id: 'building', name: 'Draft docs', status: 'building', document_count: 1, chunk_count: 0, active_index_id: null, description: '', metadata_schema: {}, doc_types: [] },
    ]);
    render(<ResourceSelect resource="collection" value="ready" onChange={vi.fn()} />);
    expect(await screen.findByRole('option', { name: /Policies/ })).toBeEnabled();
    expect(screen.getByRole('option', { name: /Draft docs/ })).toBeDisabled();
  });

  it('never falls back to a free-form collection id when Knowledge Studio fails', async () => {
    vi.mocked(knowledgeApi.listCollections).mockRejectedValueOnce(new Error('offline'));
    render(<ResourceSelect resource="collection" value="protected-id" onChange={vi.fn()} />);
    expect(await screen.findByText(/could not be reached/i)).toHaveTextContent('protected-id');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('preserves and visibly marks a deleted selection', async () => {
    vi.mocked(knowledgeApi.listCollections).mockResolvedValueOnce([]);
    render(<ResourceSelect resource="collection" value="deleted-id" onChange={vi.fn()} />);
    expect(await screen.findByText(/no longer exists/i)).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /deleted-id.*not found/i })).toBeInTheDocument();
  });
});