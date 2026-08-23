import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../api/client';
import { Library } from './Library';

vi.mock('../../api/client', () => ({
  api: {
    listWorkflows: vi.fn(),
  },
}));

describe('Library loading recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('retries the existing loader after an API failure', async () => {
    vi.mocked(api.listWorkflows)
      .mockRejectedValueOnce(new Error('500 workflow store unavailable'))
      .mockResolvedValueOnce([]);

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/workflow store unavailable/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(api.listWorkflows).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('heading', { name: 'Workflow Library' })).toBeInTheDocument();
    expect(screen.getByText('No workflows are available in this workspace.')).toBeInTheDocument();
  });
});