import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MyWork } from './MyWork';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock('../../api/client', () => ({
  api: {
    runHistory: vi.fn(),
    pendingGate: vi.fn(),
  },
}));

function run(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: 'run-aaaaaaaa-0000',
    session_id: 'sess-1',
    workflow_name: 'crm_aware_customer_triage',
    status: 'running',
    started_at: Date.now() / 1000 - 120,
    ended_at: null,
    duration_s: null,
    node_count: 5,
    completed_node_count: 2,
    active_nodes: [],
    error: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

beforeEach(() => {
  mockNavigate.mockReset();
  vi.mocked(api.runHistory).mockReset();
  vi.mocked(api.pendingGate).mockReset().mockResolvedValue({ run_id: 'x', paused: false });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderMyWork() {
  return render(
    <MemoryRouter>
      <MyWork />
    </MemoryRouter>,
  );
}

describe('MyWork', () => {
  it('buckets runs by status and pause_kind', async () => {
    vi.mocked(api.runHistory).mockResolvedValue({
      count: 4,
      runs: [
        run({ run_id: 'run-attn0000', status: 'paused', pause_kind: 'hitl_gate' }),
        run({ run_id: 'run-pause0000', status: 'paused', pause_kind: 'user_requested' }),
        run({ run_id: 'run-fail00000', status: 'failed', error: 'CRM timeout' }),
        run({ run_id: 'run-done00000', status: 'completed' }),
      ],
    });

    renderMyWork();

    expect(await screen.findByText('Needs Your Attention')).toBeInTheDocument();
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('Exceptions')).toBeInTheDocument();
    expect(screen.getByText('Recently Completed')).toBeInTheDocument();
    expect(screen.getByText('CRM timeout')).toBeInTheDocument();
  });

  it('shows the real pending question for items needing attention', async () => {
    vi.mocked(api.runHistory).mockResolvedValue({
      count: 1,
      runs: [run({ run_id: 'run-attn0000', status: 'paused', pause_kind: 'hitl_gate' })],
    });
    vi.mocked(api.pendingGate).mockResolvedValue({
      run_id: 'run-attn0000',
      paused: true,
      pause_kind: 'hitl_gate',
      node_id: 'human_review',
      question: 'Check what the customer wrote against the CRM.',
      context: null,
      allowed_actions: ['approve', 'edit', 'reject'],
      content: null,
      allow_document_override: true,
      max_edit_chars: 1000,
    });

    renderMyWork();

    expect(await screen.findByText('Check what the customer wrote against the CRM.')).toBeInTheDocument();
  });

  it('navigates to Business View when a card is opened', async () => {
    vi.mocked(api.runHistory).mockResolvedValue({
      count: 1,
      runs: [run({ run_id: 'run-running00', status: 'running' })],
    });
    const user = userEvent.setup();

    renderMyWork();
    const card = await screen.findByText('Crm aware customer triage');
    await user.click(card.closest('button')!);

    expect(mockNavigate).toHaveBeenCalledWith(
      '/business/run-running00',
      expect.objectContaining({ state: expect.objectContaining({ attach: true }) }),
    );
  });

  it('shows an empty state when there is no work at all', async () => {
    vi.mocked(api.runHistory).mockResolvedValue({ count: 0, runs: [] });

    renderMyWork();

    expect(await screen.findByText(/No work yet/i)).toBeInTheDocument();
  });
});
