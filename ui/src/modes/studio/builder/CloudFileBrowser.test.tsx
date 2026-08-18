import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../../api/client';
import { CloudFileBrowser } from './CloudFileBrowser';

vi.mock('../../../api/client', () => ({
  api: {
    browseIntegrationFiles: vi.fn(),
    downloadIntegrationFileUrl: vi.fn(
      (connectionId: string, fileId: string) => `https://example.com/download/${connectionId}/${fileId}`,
    ),
  },
}));

describe('CloudFileBrowser', () => {
  beforeEach(() => {
    vi.mocked(api.browseIntegrationFiles).mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it('lists folders and files returned for the root', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'f1', name: 'Reports', is_folder: true },
        { id: 'f2', name: 'Q3.pdf', is_folder: false, mime_type: 'application/pdf', size_bytes: 2048 },
      ],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    expect(await screen.findByText('Reports')).toBeInTheDocument();
    expect(screen.getByText('Q3.pdf')).toBeInTheDocument();
    expect(api.browseIntegrationFiles).toHaveBeenCalledWith('conn1', { folderId: undefined, query: '' });
  });

  it('shows an empty state when a folder has nothing in it', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({ files: [] });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    expect(await screen.findByText('This folder is empty.')).toBeInTheDocument();
  });

  it('shows an error state when the browse call fails', async () => {
    vi.mocked(api.browseIntegrationFiles).mockRejectedValueOnce(new Error('connection revoked'));
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    expect(await screen.findByText('connection revoked')).toBeInTheDocument();
  });

  it('clicking a folder navigates into it and adds a breadcrumb', async () => {
    const user = userEvent.setup();
    vi.mocked(api.browseIntegrationFiles)
      .mockResolvedValueOnce({ files: [{ id: 'f1', name: 'Reports', is_folder: true }] })
      .mockResolvedValueOnce({ files: [{ id: 'f2', name: 'Q3.pdf', is_folder: false }] });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    await user.click(await screen.findByText('Reports'));
    expect(await screen.findByText('Q3.pdf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reports' })).toBeInTheDocument();
    await waitFor(() => expect(api.browseIntegrationFiles).toHaveBeenLastCalledWith(
      'conn1', { folderId: 'f1', query: '' },
    ));
  });

  it('clicking a file calls onSelect in file mode', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [{ id: 'f2', name: 'Q3.pdf', is_folder: false }],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={onSelect} />);
    await user.click(await screen.findByText('Q3.pdf'));
    expect(onSelect).toHaveBeenCalledWith([{ id: 'f2', name: 'Q3.pdf' }]);
  });

  it('a folder row is not clickable to select in file mode, only navigable', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    vi.mocked(api.browseIntegrationFiles)
      .mockResolvedValueOnce({ files: [{ id: 'f1', name: 'Reports', is_folder: true }] })
      .mockResolvedValueOnce({ files: [] });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={onSelect} />);
    await user.click(await screen.findByText('Reports'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('offers View and Download for a file, but not for a folder', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'f1', name: 'Reports', is_folder: true },
        { id: 'f2', name: 'Q3.pdf', is_folder: false, web_url: 'https://drive.google.com/file/f2' },
      ],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    await screen.findByText('Q3.pdf');

    const fileRow = screen.getByText('Q3.pdf').closest('tr')!;
    expect(within(fileRow).getByText('View')).toHaveAttribute('href', 'https://drive.google.com/file/f2');
    expect(within(fileRow).getByText('Download')).toHaveAttribute('href', 'https://example.com/download/conn1/f2');

    const folderRow = screen.getByText('Reports').closest('tr')!;
    expect(within(folderRow).queryByText('View')).not.toBeInTheDocument();
    expect(within(folderRow).queryByText('Download')).not.toBeInTheDocument();
  });

  it('clicking Download does not also trigger row selection', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [{ id: 'f2', name: 'Q3.pdf', is_folder: false }],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={onSelect} />);
    await user.click(await screen.findByText('Download'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('multiple mode: checking rows and clicking Add fires onSelect once with everything checked', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'f1', name: 'Q3.pdf', is_folder: false },
        { id: 'f2', name: 'Q4.pdf', is_folder: false },
      ],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" multiple onSelect={onSelect} />);
    await screen.findByText('Q3.pdf');

    // Clicking a row in multiple mode does not select immediately.
    await user.click(screen.getByText('Q3.pdf'));
    expect(onSelect).not.toHaveBeenCalled();

    await user.click(screen.getByLabelText('Select Q3.pdf'));
    await user.click(screen.getByLabelText('Select Q4.pdf'));
    expect(screen.getByText('2 selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Add 2 file/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith([
      { id: 'f1', name: 'Q3.pdf' },
      { id: 'f2', name: 'Q4.pdf' },
    ]);
  });

  it('multiple mode: only selectable entries get a checkbox (folders in file mode do not)', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'f1', name: 'Reports', is_folder: true },
        { id: 'f2', name: 'Q3.pdf', is_folder: false },
      ],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" multiple onSelect={vi.fn()} />);
    await screen.findByText('Q3.pdf');
    expect(screen.queryByLabelText('Select Reports')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Select Q3.pdf')).toBeInTheDocument();
  });

  it('the Add button is disabled until something is checked', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [{ id: 'f1', name: 'Q3.pdf', is_folder: false }],
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" multiple onSelect={vi.fn()} />);
    await screen.findByText('Q3.pdf');
    expect(screen.getByRole('button', { name: /Add/ })).toBeDisabled();
  });

  it('shows a Load more button when a next page token is returned', async () => {
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [{ id: 'f1', name: 'A.pdf', is_folder: false }],
      next_page_token: 'page-2',
    });
    render(<CloudFileBrowser connectionId="conn1" mode="file" onSelect={vi.fn()} />);
    expect(await screen.findByText('Load more')).toBeInTheDocument();
  });
});
