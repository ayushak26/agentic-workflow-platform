import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../api/client';
import { VersionHistoryPanel } from './VersionHistoryPanel';

vi.mock('../../api/client', () => ({
  api: {
    listWorkflowVersions: vi.fn(),
    getWorkflowVersion: vi.fn(),
    restoreWorkflowVersion: vi.fn(),
  },
}));

const YAML = "name: Test\nnodes: []\nedges: []\n";

describe('VersionHistoryPanel compatibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('labels incompatible history and prevents restore', async () => {
    vi.mocked(api.listWorkflowVersions).mockResolvedValue([{
      version_id: 'version-1',
      created_at: '2026-08-23T00:00:00Z',
      sha256: 'abc',
      current: false,
      workflow_version: '1.0',
      node_count: 2,
      description: 'Legacy snapshot',
    }]);
    vi.mocked(api.getWorkflowVersion).mockResolvedValue({
      yaml: YAML,
      restorable: false,
      preflight_issue_codes: ['UNKNOWN_NODE_TYPE'],
      preflight_errors: ["Unknown node type 'RemovedHistoricalNode'."],
    });

    render(
      <VersionHistoryPanel
        workflowName="test"
        currentYaml={YAML}
        onClose={vi.fn()}
        onRestored={vi.fn()}
      />,
    );

    await userEvent.click(await screen.findByRole('button', { name: /Legacy snapshot/ }));
    expect(await screen.findByText(/cannot be restored under the current runtime/)).toBeInTheDocument();
    expect(screen.getByText('UNKNOWN_NODE_TYPE')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Version cannot be restored' })).toBeDisabled();
    expect(api.restoreWorkflowVersion).not.toHaveBeenCalled();
  });

  it('restores a compatible version', async () => {
    const onRestored = vi.fn();
    vi.mocked(api.listWorkflowVersions).mockResolvedValue([{
      version_id: 'version-2',
      created_at: '2026-08-23T00:00:00Z',
      sha256: 'def',
      current: false,
      workflow_version: '1.0',
      node_count: 1,
      description: 'Good snapshot',
    }]);
    vi.mocked(api.getWorkflowVersion).mockResolvedValue({
      yaml: YAML, restorable: true, preflight_issue_codes: [], preflight_errors: [],
    });
    vi.mocked(api.restoreWorkflowVersion).mockResolvedValue({ yaml: YAML, version_id: 'version-2' });

    render(
      <VersionHistoryPanel
        workflowName="test"
        currentYaml={YAML}
        onClose={vi.fn()}
        onRestored={onRestored}
      />,
    );

    await userEvent.click(await screen.findByRole('button', { name: /Good snapshot/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Restore this version' }));
    await waitFor(() => expect(onRestored).toHaveBeenCalledWith(YAML));
  });
});