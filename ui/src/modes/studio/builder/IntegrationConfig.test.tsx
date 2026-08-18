import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IntegrationConnectionInfo, NodePreset } from '../../../api/types';
import { api } from '../../../api/client';
import { IntegrationConfig } from './IntegrationConfig';

vi.mock('../../../api/client', () => ({
  api: {
    integrationConnectUrl: vi.fn((provider: string) => `https://example.com/connect/${provider}`),
    disconnectIntegrationConnection: vi.fn(),
    browseIntegrationFiles: vi.fn(),
    downloadIntegrationFileUrl: vi.fn(
      (connectionId: string, fileId: string) => `https://example.com/download/${connectionId}/${fileId}`,
    ),
  },
}));

const PRESETS: NodePreset[] = [
  { id: 'google_drive', label: 'Google Drive', summary: 'Browse Google Drive.', config: { provider: 'google_drive' } },
  { id: 'onedrive', label: 'OneDrive', summary: 'Browse OneDrive.', config: { provider: 'onedrive' } },
];

const GOOGLE_CONNECTION: IntegrationConnectionInfo = {
  id: 'google_drive_a',
  provider: 'google_drive',
  display_name: 'Google Drive (a@example.com)',
  address: 'a@example.com',
  needs_reauth: false,
};

describe('IntegrationConfig', () => {
  beforeEach(() => {
    vi.mocked(api.disconnectIntegrationConnection).mockReset();
    vi.mocked(api.browseIntegrationFiles).mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it('shows a provider chooser when no provider has been picked yet', () => {
    render(
      <IntegrationConfig
        config={{}}
        connections={[]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('Choose provider')).toBeInTheDocument();
    expect(screen.getByText('Google Drive')).toBeInTheDocument();
    expect(screen.getByText('OneDrive')).toBeInTheDocument();
  });

  it('picking a provider preset writes it into config', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <IntegrationConfig
        config={{}}
        connections={[]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('Google Drive'));
    expect(onChange).toHaveBeenCalledWith({ provider: 'google_drive' });
  });

  it('shows a connect button and no-connection notice once a provider is chosen', () => {
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive' }}
        connections={[]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText(/No Google Drive account is connected/)).toBeInTheDocument();
    expect(screen.getByText('+ Connect Google Drive')).toBeInTheDocument();
  });

  it('lists only connections matching the chosen provider', () => {
    render(
      <IntegrationConfig
        config={{ provider: 'onedrive' }}
        connections={[GOOGLE_CONNECTION, { ...GOOGLE_CONNECTION, id: 'onedrive_a', provider: 'onedrive', display_name: 'OneDrive (b@example.com)' }]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('OneDrive (b@example.com)')).toBeInTheDocument();
    expect(screen.queryByText('Google Drive (a@example.com)')).not.toBeInTheDocument();
  });

  it('shows Reauthentication required for a connection that needs it', () => {
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a' }}
        connections={[{ ...GOOGLE_CONNECTION, needs_reauth: true }]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('Reauthentication required')).toBeInTheDocument();
  });

  it('switching operation to Search Files reveals the query field', async () => {
    const user = userEvent.setup();
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'list_folder' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.queryByText('Query')).not.toBeInTheDocument();
    await user.click(screen.getByText('Search Files'));
  });

  it('renders the query field for search_files operations', () => {
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'search_files' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('Query')).toBeInTheDocument();
  });

  it('renders the file field for get_file operations', () => {
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'get_file' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('File(s)')).toBeInTheDocument();
  });

  it('opens the multi-select file browser when + Add files is clicked with a connection chosen', async () => {
    const user = userEvent.setup();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({ files: [] });
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'get_file' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={vi.fn()}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('+ Add files'));
    expect(await screen.findByText('Add file(s)')).toBeInTheDocument();
  });

  it('picking two files from the browser stores both ids as a list', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'f1', name: 'Q3.pdf', is_folder: false },
        { id: 'f2', name: 'Q4.pdf', is_folder: false },
      ],
    });
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'get_file' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('+ Add files'));
    await screen.findByText('Q3.pdf');
    await user.click(screen.getByLabelText('Select Q3.pdf'));
    await user.click(screen.getByLabelText('Select Q4.pdf'));
    await user.click(screen.getByRole('button', { name: /Add 2 file/ }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ file_id: ['f1', 'f2'] }));
  });

  it('a single picked file is stored as a plain string, not a one-element array', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [{ id: 'f1', name: 'Q3.pdf', is_folder: false }],
    });
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'select_file' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('+ Add files'));
    await screen.findByText('Q3.pdf');
    await user.click(screen.getByLabelText('Select Q3.pdf'));
    await user.click(screen.getByRole('button', { name: /Add 1 file/ }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ file_id: 'f1' }));
  });

  it('shows already-picked files as removable chips and removing the last one clears the field', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'select_file', file_id: ['f1', 'f2'] }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    expect(screen.getByText('f1')).toBeInTheDocument();
    expect(screen.getByText('f2')).toBeInTheDocument();
    await user.click(screen.getByLabelText('Remove f1'));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ file_id: 'f2' }));
  });

  it('switching to a template value clears the browser and accepts a typed reference', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'select_file' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('Use a template value instead'));
    expect(screen.queryByText('+ Add files')).not.toBeInTheDocument();
  });

  it('folder_id supports multi-select for select_folder', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    vi.mocked(api.browseIntegrationFiles).mockResolvedValueOnce({
      files: [
        { id: 'd1', name: 'Reports', is_folder: true },
        { id: 'd2', name: 'Archive', is_folder: true },
      ],
    });
    render(
      <IntegrationConfig
        config={{ provider: 'google_drive', connection: 'google_drive_a', operation: 'select_folder' }}
        connections={[GOOGLE_CONNECTION]}
        contract={null}
        onChange={onChange}
        onConnectionsChanged={vi.fn()}
        presets={PRESETS}
      />,
    );
    await user.click(screen.getByText('+ Add folders'));
    await screen.findByText('Reports');
    await user.click(screen.getByLabelText('Select Reports'));
    await user.click(screen.getByLabelText('Select Archive'));
    await user.click(screen.getByRole('button', { name: /Add 2 folder/ }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ folder_id: ['d1', 'd2'] }));
  });
});
