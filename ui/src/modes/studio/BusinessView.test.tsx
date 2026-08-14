import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BusinessView } from './BusinessView';
import { api } from '../../api/client';
import { useCockpitRun } from './cockpit/useCockpitRun';
import type { BusinessProjection } from '../../api/types';

vi.mock('./cockpit/useCockpitRun');
vi.mock('../../api/client', () => ({
  api: {
    businessProjection: vi.fn(),
    pauseRun: vi.fn(),
    resumePausedRun: vi.fn(),
    deleteRun: vi.fn(),
    downloadArtifact: vi.fn(),
    correctFact: vi.fn(),
    resumeWorkflow: vi.fn(),
    assignRun: vi.fn(),
  },
}));
// HITLPanel/OutputViewer/AskAiPanel are reused, already-tested collaborators —
// stubbed here so these tests exercise only BusinessView's own logic.
vi.mock('./HITLPanel', () => ({
  HITLPanel: () => <div data-testid="hitl-panel">HITL review</div>,
}));
vi.mock('./OutputViewer', () => ({
  OutputViewer: () => <div data-testid="output-viewer" />,
}));
vi.mock('./run-history/AskAiPanel', () => ({
  AskAiPanel: () => <div data-testid="ask-ai-panel" />,
}));

const mockedUseCockpitRun = useCockpitRun as unknown as ReturnType<typeof vi.fn>;

function baseCockpitRun(overrides: Record<string, unknown> = {}) {
  return {
    runId: 'run-123',
    navState: {},
    navigate: vi.fn(),
    triggerError: null,
    liveRun: { run_id: 'run-123', status: 'running', retry_available: false },
    gate: null,
    setGateHidden: vi.fn(),
    gateFetchError: null,
    retryGateFetch: vi.fn(),
    finished: null,
    events: [],
    streamError: null,
    applyResumeResult: vi.fn(),
    setTriggerError: vi.fn(),
    pipelineDoc: null,
    continueToNextStage: vi.fn(),
    continuingStage: false,
    continueError: null,
    ...overrides,
  };
}

function baseProjection(overrides: Partial<BusinessProjection> = {}): BusinessProjection {
  return {
    work_item: { id: 'run-123', type: 'Multilingual customer request triage', status: 'In Progress', started_at: null, updated_at: null },
    process: { name: 'Multilingual Customer Request Triage', goal: 'Route the request to the right team.' },
    status: 'running',
    current_activity: { node_id: 'understand_request', display_name: 'Understand Customer Request', message: 'Reading the message.', waiting_for_you: false },
    progress: [
      { id: 'prepare', display_name: 'Prepare', state: 'completed', completed_count: 1, total_count: 1 },
      { id: 'understand', display_name: 'Understand', state: 'active', completed_count: 0, total_count: 1 },
    ],
    understanding: {},
    editable_facts: [],
    stale_decisions: [],
    missing_information: [],
    checks: [],
    facts: [],
    decision: null,
    decision_explanation: [],
    uncertainties: [],
    recommendations: [],
    proposed_actions: [],
    completed_actions: [],
    required_user_actions: [],
    allowed_controls: ['pause', 'stop'],
    timeline: [{ ts: '2026-08-14T10:00:00Z', label: 'Request received' }],
    ...overrides,
  };
}

beforeEach(() => {
  mockedUseCockpitRun.mockReset();
  vi.mocked(api.businessProjection).mockReset();
  vi.mocked(api.pauseRun).mockReset().mockResolvedValue({ run_id: 'run-123', pause_requested: true, message: '' });
  vi.mocked(api.resumePausedRun).mockReset();
  vi.mocked(api.deleteRun).mockReset().mockResolvedValue({ run_id: 'run-123', deleted: true });
  vi.mocked(api.correctFact).mockReset();
  vi.mocked(api.resumeWorkflow).mockReset();
  vi.mocked(api.assignRun).mockReset();
});

afterEach(() => {
  // restoreAllMocks undoes vi.spyOn(window, 'confirm'); the api/useCockpitRun
  // mocks are plain vi.fn()s re-armed explicitly in beforeEach regardless.
  vi.restoreAllMocks();
});

describe('BusinessView', () => {
  it('shows a loading state before the projection resolves', () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockReturnValue(new Promise(() => {}));

    render(<BusinessView />);

    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('Opening this work item');
  });

  it('renders the work item header, current activity, and progress once loaded', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());

    render(<BusinessView />);

    expect(await screen.findByText('Multilingual Customer Request Triage')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('Reading the message.')).toBeInTheDocument();
    expect(screen.getByText('Understand')).toBeInTheDocument();
    expect(screen.getByText('Prepare')).toBeInTheDocument();
  });

  it('only shows controls the projection actually allows', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({ allowed_controls: ['pause', 'stop'] }));

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');

    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry safely' })).not.toBeInTheDocument();
  });

  it('calls pauseRun when Pause is clicked', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');
    await user.click(screen.getByRole('button', { name: 'Pause' }));

    expect(api.pauseRun).toHaveBeenCalledWith('run-123');
  });

  it('surfaces missing information and the decision explanation behind "Why?"', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
      missing_information: ['product_model', 'serial_number'],
      decision: { node_id: 'automation_safety', decisions: { human_review: true }, rules_triggered: ['Low confidence needs a person'], summary: [] },
      decision_explanation: [{ name: 'Low confidence needs a person', description: 'Below 0.80 we do not act automatically.' }],
    }));
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');

    expect(screen.getByText('product model')).toBeInTheDocument();
    expect(screen.getByText('serial number')).toBeInTheDocument();
    expect(screen.queryByText('Low confidence needs a person')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Why?' }));

    expect(screen.getByText('Low confidence needs a person')).toBeInTheDocument();
    expect(screen.getByText('Below 0.80 we do not act automatically.')).toBeInTheDocument();
  });

  it('shows an approval card with a working Review action when a HITL gate is pending', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun({
      gate: { nodeId: 'human_review', context: {}, question: 'Check the extraction.', allowedActions: ['approve', 'edit', 'reject'], content: null, allowDocumentOverride: true, maxEditChars: 1000 },
    }));
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
      status: 'paused',
      work_item: { id: 'run-123', type: 'Triage', status: 'Waiting for You', started_at: null, updated_at: null },
      required_user_actions: [{ type: 'approval_review', node_id: 'human_review', question: 'Check the extraction.', allowed_actions: ['approve', 'edit', 'reject'] }],
      allowed_controls: ['approve', 'edit', 'reject', 'ask_why', 'stop'],
    }));
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Check the extraction.');

    expect(screen.getByText('Approval required')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: /Review/ })[0]);

    expect(screen.getByTestId('hitl-panel')).toBeInTheDocument();
  });

  it('confirms before stopping, and does not call deleteRun when the user cancels', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');
    await user.click(screen.getByRole('button', { name: 'Stop' }));

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteRun).not.toHaveBeenCalled();
  });

  it('calls deleteRun and navigates away once the user confirms Stop', async () => {
    const navigate = vi.fn();
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun({ navigate }));
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');
    await user.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => expect(api.deleteRun).toHaveBeenCalledWith('run-123'));
    expect(navigate).toHaveBeenCalledWith('/history');
  });

  it('re-fetches the projection when a new SSE event arrives', async () => {
    const cockpitRun = baseCockpitRun({ events: [] });
    mockedUseCockpitRun.mockReturnValue(cockpitRun);
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());

    const { rerender } = render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');
    expect(api.businessProjection).toHaveBeenCalledTimes(1);

    mockedUseCockpitRun.mockReturnValue(baseCockpitRun({
      events: [{ type: 'node_completed', run_id: 'run-123', node_id: 'understand_request', output_preview: '', ts: '2026-08-14T10:00:01Z' }],
    }));
    rerender(<BusinessView />);

    await waitFor(() => expect(api.businessProjection).toHaveBeenCalledTimes(2));
  });

  it('renders the timeline in reverse-chronological order', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
      timeline: [
        { ts: '2026-08-14T10:00:00Z', label: 'Request received' },
        { ts: '2026-08-14T10:00:05Z', label: 'Understand Customer Request completed' },
      ],
    }));

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');

    const history = screen.getByText('History').closest('section');
    const items = within(history as HTMLElement).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Understand Customer Request completed');
    expect(items[1]).toHaveTextContent('Request received');
  });

  it('lets a person correct an editable fact and re-fetches the projection', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection)
      .mockResolvedValueOnce(baseProjection({
        understanding: { node_id: 'understand_request', result: { pressure: null }, confidence: 0.9 },
        editable_facts: ['pressure'],
      }))
      .mockResolvedValueOnce(baseProjection({
        understanding: { node_id: 'understand_request', result: { pressure: '6 bar' }, confidence: 0.9 },
        editable_facts: ['pressure'],
      }));
    vi.mocked(api.correctFact).mockResolvedValue({
      ok: true,
      edit: { field: 'pressure', value: '6 bar', stale_decisions: ['complexity'], edited_at: '2026-08-14T10:00:00Z' },
    });
    const user = userEvent.setup();

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');
    expect(screen.getByText('not stated')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Edit pressure' }));
    const understandingSection = screen.getByText('What I understood').closest('section') as HTMLElement;
    await user.type(within(understandingSection).getByRole('textbox'), '6 bar');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(api.correctFact).toHaveBeenCalledWith('run-123', 'pressure', '6 bar');
    await waitFor(() => expect(api.businessProjection).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('6 bar')).toBeInTheDocument();
  });

  it('marks a decision as stale when it depends on a corrected fact', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
    vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
      decision: { node_id: 'assess_request', decisions: { complexity: 'technical', human_review: false }, rules_triggered: [], summary: [] },
      stale_decisions: ['complexity'],
    }));

    render(<BusinessView />);
    await screen.findByText('Multilingual Customer Request Triage');

    const decisionSection = screen.getByText('How this was decided').closest('section') as HTMLElement;
    expect(within(decisionSection).getByText('Stale')).toBeInTheDocument();
    expect(within(decisionSection).getByText(/Retry safely/)).toBeInTheDocument();
  });

  describe('the ask bar as a bounded command control', () => {
    async function typeAndSend(user: ReturnType<typeof userEvent.setup>, text: string) {
      const input = screen.getByRole('textbox');
      await user.type(input, text);
      await user.click(screen.getByRole('button', { name: /^(Send|Working…)$/ }));
    }

    it('runs /pause through the real pause control', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({ allowed_controls: ['pause', 'stop'] }));
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      await typeAndSend(user, '/pause');

      expect(api.pauseRun).toHaveBeenCalledWith('run-123');
      expect(await screen.findByText('Paused.')).toBeInTheDocument();
    });

    it('runs /approve when an approval is pending', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
        required_user_actions: [{ type: 'approval_review', node_id: 'human_review', question: 'Check this.', allowed_actions: ['approve', 'reject'] }],
      }));
      vi.mocked(api.resumeWorkflow).mockResolvedValue({ ok: true });
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      await typeAndSend(user, '/approve');

      expect(api.resumeWorkflow).toHaveBeenCalledWith('run-123', { decision: 'approve' });
      expect(await screen.findByText('Approved.')).toBeInTheDocument();
    });

    it('rejects /reject with no reason instead of calling the API', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection({
        required_user_actions: [{ type: 'approval_review', node_id: 'human_review', question: 'Check this.', allowed_actions: ['approve', 'reject'] }],
      }));
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      await typeAndSend(user, '/reject');

      expect(api.resumeWorkflow).not.toHaveBeenCalled();
      expect(await screen.findByText(/A reason helps/)).toBeInTheDocument();
    });

    it('runs /assign and refetches the projection', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
      vi.mocked(api.assignRun).mockResolvedValue({ ok: true, assigned_to: 'Maria' });
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      expect(api.businessProjection).toHaveBeenCalledTimes(1);
      await typeAndSend(user, '/assign Maria');

      expect(api.assignRun).toHaveBeenCalledWith('run-123', 'Maria');
      expect(await screen.findByText('Assigned to Maria.')).toBeInTheDocument();
      await waitFor(() => expect(api.businessProjection).toHaveBeenCalledTimes(2));
    });

    it('rejects an unrecognized command without opening the chat', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      await typeAndSend(user, '/nonsense');

      expect(await screen.findByText(/Unknown command/)).toBeInTheDocument();
      expect(screen.queryByTestId('ask-ai-panel')).not.toBeInTheDocument();
    });

    it('opens the full chat for plain text with no leading slash', async () => {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun());
      vi.mocked(api.businessProjection).mockResolvedValue(baseProjection());
      const user = userEvent.setup();

      render(<BusinessView />);
      await screen.findByText('Multilingual Customer Request Triage');
      await typeAndSend(user, 'what happened here?');

      expect(await screen.findByTestId('ask-ai-panel')).toBeInTheDocument();
    });
  });
});
