import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BusinessView } from './BusinessView';
import { api } from '../../api/client';
import { useCockpitRun } from './cockpit/useCockpitRun';
import { action, attentionItem, basfProjection, fact } from './business/fixtures';

vi.mock('./cockpit/useCockpitRun');
vi.mock('../../api/client', () => ({
  api: {
    businessProjection: vi.fn(),
    businessNarration: vi.fn(),
    businessExplanation: vi.fn(),
    businessTechnicalDetail: vi.fn(),
    businessAction: vi.fn(),
    pauseRun: vi.fn(),
    resumePausedRun: vi.fn(),
    restartRun: vi.fn(),
    deleteRun: vi.fn(),
    downloadArtifact: vi.fn(),
    correctFact: vi.fn(),
    resumeWorkflow: vi.fn(),
    assignRun: vi.fn(),
  },
}));
// Reused, already-tested collaborators — stubbed so these tests exercise only
// the Business View's own behaviour.
vi.mock('./HITLPanel', () => ({ HITLPanel: () => <div data-testid="hitl-panel">HITL review</div> }));
vi.mock('./run-history/AskAiPanel', () => ({ AskAiPanel: () => <div data-testid="ask-ai-panel" /> }));

const mockedUseCockpitRun = useCockpitRun as unknown as ReturnType<typeof vi.fn>;

function baseCockpitRun(overrides: Record<string, unknown> = {}) {
  return {
    runId: 'run-123',
    navState: {},
    navigate: vi.fn(),
    triggerError: null,
    liveRun: { run_id: 'run-123', status: 'completed', retry_available: false, workflow_name: 'Pump routing' },
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

beforeEach(() => {
  mockedUseCockpitRun.mockReset().mockReturnValue(baseCockpitRun());
  vi.mocked(api.businessProjection).mockReset().mockResolvedValue(basfProjection());
  vi.mocked(api.businessNarration).mockReset().mockResolvedValue({
    state_version: 'v1', headline: 'Ready for Inside Sales', summary: 'Narrated summary.',
    next_step: '', source: 'deterministic', model: null, cached: true,
  });
  vi.mocked(api.businessExplanation).mockReset();
  vi.mocked(api.businessTechnicalDetail).mockReset();
  vi.mocked(api.businessAction).mockReset();
  vi.mocked(api.correctFact).mockReset();
  vi.mocked(api.assignRun).mockReset();
  vi.mocked(api.deleteRun).mockReset().mockResolvedValue({ run_id: 'run-123', deleted: true });
});

afterEach(() => vi.restoreAllMocks());

async function renderView() {
  render(<BusinessView />);
  await screen.findByText('BASF SE — Quotation request');
}

describe('the first screen communicates the situation', () => {
  it('leads with who, what, status and a business summary', async () => {
    await renderView();

    const header = screen.getByRole('banner');
    expect(within(header).getByRole('heading', { name: 'BASF SE — Quotation request' })).toBeInTheDocument();
    expect(within(header).getByText('Ready for Inside Sales')).toBeInTheDocument();
    expect(within(header).getByText(/Quotation request · #run-123/)).toBeInTheDocument();
    expect(within(header).getByText(/requesting a quotation for five new pumps/)).toBeInTheDocument();
    expect(within(header).getByText('4 items need attention')).toBeInTheDocument();
  });

  it('never renders raw or parsed model JSON', async () => {
    // The projection carries no raw payload at all, so this asserts the
    // property the contract guarantees: nothing on screen looks like JSON.
    await renderView();

    expect(screen.queryByText(/"intent":/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^raw$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^parsed$/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\{"[a-z_]+":/);
  });

  it('shows understood information as labelled business fields', async () => {
    await renderView();

    const section = screen.getByText('What I understood').closest('section') as HTMLElement;
    expect(within(section).getByText('Customer')).toBeInTheDocument();
    expect(within(section).getByText('BASF SE')).toBeInTheDocument();
    expect(within(section).getByText('Quantity')).toBeInTheDocument();
    // A fact the workflow could not establish is shown in words, not dropped.
    expect(within(section).getByText('Not stated')).toBeInTheDocument();
  });
});

describe('the attention centre', () => {
  it('gives each gap direct resolution actions', async () => {
    await renderView();

    const section = screen.getByText('Needs attention (2)').closest('section') as HTMLElement;
    expect(within(section).getByText('Pump model')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: 'Review datasheet' })).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: /Ask customer/ })).toBeInTheDocument();
  });

  it('labels an action that only prepares something as a draft', async () => {
    await renderView();

    expect(
      screen.getByRole('button', { name: 'Ask customer (prepares a draft for your approval)' }),
    ).toBeInTheDocument();
  });

  it('renders nothing when there is nothing to attend to', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({ attention: [] }));
    await renderView();

    expect(screen.queryByText(/Needs attention/)).not.toBeInTheDocument();
  });
});

describe('the handling decision', () => {
  it('shows the route, its reason and its supporting facts', async () => {
    await renderView();

    const section = screen.getByText('Handling decision').closest('section') as HTMLElement;
    expect(within(section).getByText('Inside Sales')).toBeInTheDocument();
    expect(within(section).getByText(/no named territory owner/)).toBeInTheDocument();
    expect(within(section).getByText('Safety issue: No')).toBeInTheDocument();
    expect(within(section).getByText('Business rule')).toBeInTheDocument();
  });

  it('fetches the explanation only when someone asks Why', async () => {
    vi.mocked(api.businessExplanation).mockResolvedValue({
      decision: 'Inside Sales',
      summary: 'Sent to Inside Sales because it is a standard quotation request with no safety issue.',
      facts: [{ id: 'check:safety_router', label: 'Safety issue', value: 'No', source: 'Business rule' }],
      rules: [{ id: 'intent_router', name: 'Request → RFQ', description: 'rule matched' }],
      source: 'ai',
      model: 'gpt-5.6-luna',
    });
    const user = userEvent.setup();
    await renderView();

    expect(api.businessExplanation).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Why?' }));

    expect(api.businessExplanation).toHaveBeenCalledWith('run-123');
    expect(await screen.findByText(/standard quotation request with no safety issue/)).toBeInTheDocument();
    expect(screen.getByText('Wording by AI · gpt-5.6-luna')).toBeInTheDocument();
  });

  it('marks a human route override rather than presenting it as the system\'s', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      decision: {
        ...basfProjection().decision!,
        headline: 'Technical Sales',
        original_headline: 'Inside Sales',
        overridden: true,
        overridden_by: 'maria',
        source: 'human',
        source_label: 'Changed by a person',
      },
    }));
    await renderView();

    const section = screen.getByText('Handling decision').closest('section') as HTMLElement;
    expect(within(section).getByText('Technical Sales')).toBeInTheDocument();
    expect(within(section).getByText(/was Inside Sales · changed by maria/)).toBeInTheDocument();
  });
});

describe('AI provenance and models', () => {
  it('names the model that actually executed, not the one requested', async () => {
    await renderView();

    // `requested: 'auto'` never reaches the business surface; the executed
    // model does.
    expect(screen.getAllByText('AI · claude-sonnet-4-5').length).toBeGreaterThan(0);
    expect(screen.queryByText(/AI · auto/)).not.toBeInTheDocument();
  });

  it('does not attach a model badge to a rule-based decision', async () => {
    await renderView();

    const section = screen.getByText('Handling decision').closest('section') as HTMLElement;
    expect(within(section).queryByText(/AI ·/)).not.toBeInTheDocument();
  });

  it('shows latency and cost only where a real figure exists', async () => {
    await renderView();

    expect(screen.getByText('1.4s · $0.0018')).toBeInTheDocument();
  });
});

describe('activities replace per-node event spam', () => {
  it('summarises how many business activities completed', async () => {
    await renderView();

    expect(screen.getByText('2 of 2 business activities completed')).toBeInTheDocument();
    expect(screen.queryByText(/router started/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/router completed/i)).not.toBeInTheDocument();
  });

  it('expands one activity into its findings and technical counts', async () => {
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: /Handling checks completed/ }));

    // Two routers collapsed into one business activity, and the count of what
    // they were is available without leaving the business surface.
    expect(screen.getByText('2 technical steps · 2 rules')).toBeInTheDocument();
    expect(screen.getAllByText('Safety issue').length).toBeGreaterThan(0);
  });

  it('groups start and completion into one timeline entry with its checks', async () => {
    await renderView();

    const history = screen.getByText('History').closest('section') as HTMLElement;
    const items = within(history).getAllByRole('listitem');
    // Reverse-chronological: the handling checks are the most recent entry.
    expect(items[0]).toHaveTextContent('Handling checks completed');
    expect(items[0]).toHaveTextContent('Complexity: Standard');
    expect(within(history).queryByText(/started$/)).not.toBeInTheDocument();
  });
});

describe('technical detail stays one level deeper', () => {
  it('fetches raw output only when a person opens technical details', async () => {
    vi.mocked(api.businessTechnicalDetail).mockResolvedValue({
      activity_id: 'handling',
      title: 'Handling checks completed',
      technical: {
        node_ids: ['intent_router'], nodes: [], ai_calls: [
          {
            requested: 'auto', selected: 'claude-sonnet-4-5', executed: 'claude-sonnet-4-5',
            fallback: true, fallback_reason: 'Provider temporarily unavailable', routing_reason: null,
            latency_ms: 1400, cost_usd: 0.0018, task_type: 'extraction', provider: 'anthropic', call_count: 1,
          },
        ],
        rule_count: 4, rules: [], duration_ms: 430, has_raw_output: true,
      },
      nodes: [{ node_id: 'intent_router', type_name: 'RouterAgent', status: 'completed', duration_s: 0.1, error: null, model_selections: [], output: { route: 'RFQ' } }],
      cost_entries: [],
    });
    const user = userEvent.setup();
    await renderView();

    expect(api.businessTechnicalDetail).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Technical details' }));

    await waitFor(() => expect(api.businessTechnicalDetail).toHaveBeenCalledWith('run-123', 'run'));
    // Requested vs executed are both visible here, and only here (§23).
    expect(await screen.findByText('auto')).toBeInTheDocument();
    expect(screen.getByText('Provider temporarily unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/"route"/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Raw output' }));
    expect(screen.getByText(/"route": "RFQ"/)).toBeInTheDocument();
  });
});

describe('typed actions and permissions', () => {
  it('renders only the actions the projection allows', async () => {
    await renderView();

    expect(screen.getAllByRole('button', { name: 'Assign owner' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
  });

  it('hides every action when the person may not act', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      allowed_actions: [action('open_technical_details', 'Technical details', { params: { activity_id: 'run' } })],
      recommended_actions: [],
      other_actions: [],
      attention: [attentionItem({ id: 'a1', title: 'Pump model' })],
      decision: { ...basfProjection().decision!, actions: [] },
      next_step: { ...basfProjection().next_step!, actions: [] },
    }));
    await renderView();

    expect(screen.queryByRole('button', { name: 'Assign owner' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add note' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Technical details' })).toBeInTheDocument();
  });

  it('sends a typed command to the backend for a route override', async () => {
    vi.mocked(api.businessAction).mockResolvedValue({
      kind: 'route_override',
      override: { route: 'Technical Sales', reason: 'Needs engineering input', by: 'maria', at: '2026-08-14T18:00:00Z' },
    });
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: 'Change route' }));
    await user.type(screen.getByLabelText('Send to'), 'Technical Sales');
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Change route' }));

    await waitFor(() => expect(api.businessAction).toHaveBeenCalledWith('run-123', 'route_override', {
      route: 'Technical Sales', reason: '',
    }));
  });

  it('drafts a customer question without sending anything', async () => {
    vi.mocked(api.businessAction).mockResolvedValue({
      kind: 'clarification_draft',
      subject: 'Your quotation request',
      body: 'Could you confirm the required delivery date?',
      asks: ['requested_delivery_date'],
      sent: false,
      note: 'Draft only — review and send it yourself.',
    });
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: /Ask customer/ }));

    await waitFor(() => expect(api.businessAction).toHaveBeenCalledWith(
      'run-123', 'draft_clarification', expect.anything(),
    ));
    expect(await screen.findByText('Could you confirm the required delivery date?')).toBeInTheDocument();
    expect(screen.getByText(/Nothing was sent/)).toBeInTheDocument();
  });

  it('confirms before stopping, and does not delete when the person cancels', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      allowed_actions: [action('stop_run', 'Stop', { emphasis: 'danger' })],
    }));
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: 'Stop' }));

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteRun).not.toHaveBeenCalled();
  });
});

describe('correcting what the AI understood', () => {
  it('edits a fact through a labelled form, never JSON, and re-reads the work item', async () => {
    vi.mocked(api.correctFact).mockResolvedValue({
      ok: true,
      edit: { field: 'requested_quantity', value: '6', stale_decisions: [], edited_at: '2026-08-14T18:00:00Z' },
    });
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: 'Edit Quantity' }));
    const input = screen.getByLabelText('Quantity');
    await user.clear(input);
    await user.type(input, '6');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(api.correctFact).toHaveBeenCalledWith('run-123', 'requested_quantity', '6'));
    await waitFor(() => expect(api.businessProjection).toHaveBeenCalledTimes(2));
  });

  it('flags a determination made from a value that has since changed', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      understanding: {
        ...basfProjection().understanding,
        fields: [fact({ id: 'understanding:pump_model', label: 'Pump model', display: 'P-100', stale: true })],
      },
    }));
    await renderView();

    const section = screen.getByText('What I understood').closest('section') as HTMLElement;
    expect(within(section).getByText('Recheck')).toBeInTheDocument();
  });
});

describe('conversation and next step', () => {
  it('offers state-dependent suggested prompts that open the chat', async () => {
    const user = userEvent.setup();
    await renderView();

    await user.click(screen.getByRole('button', { name: 'Why Inside Sales?' }));

    expect(await screen.findByTestId('ask-ai-panel')).toBeInTheDocument();
  });

  it('always answers what happens next', async () => {
    await renderView();

    const section = screen.getByText('What happens next').closest('section') as HTMLElement;
    expect(within(section).getByText('Inside Sales takes this on')).toBeInTheDocument();
    expect(within(section).getByText(/prepares the quotation/)).toBeInTheDocument();
  });

  it('says what is blocking when the process cannot continue', async () => {
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      next_step: {
        headline: 'Resolve: pump model',
        description: null,
        blocked: true,
        blocked_reason: 'The process cannot continue until the pump model is identified.',
        owner: null,
        actions: [action('document_review', 'Review datasheet')],
      },
    }));
    await renderView();

    expect(screen.getByText('The process cannot continue until the pump model is identified.')).toBeInTheDocument();
  });
});

describe('approval and live updates', () => {
  it('opens the review panel when an approval is pending', async () => {
    mockedUseCockpitRun.mockReturnValue(baseCockpitRun({
      gate: { nodeId: 'human_review', context: {}, question: 'Confirm the account.', allowedActions: ['approve', 'reject'], content: null, allowDocumentOverride: true, maxEditChars: 1000 },
    }));
    vi.mocked(api.businessProjection).mockResolvedValue(basfProjection({
      status: 'paused',
      required_user_actions: [{ type: 'approval_review', node_id: 'human_review', question: 'Confirm the account.', allowed_actions: ['approve', 'reject'] }],
    }));
    const user = userEvent.setup();
    await renderView();

    expect(screen.getByText('Approval required')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Review and respond' }));

    expect(screen.getByTestId('hitl-panel')).toBeInTheDocument();
  });

  it('coalesces a burst of run events into a single refetch', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { rerender } = render(<BusinessView />);
    await waitFor(() => expect(api.businessProjection).toHaveBeenCalledTimes(1));

    // Fourteen nodes completing in quick succession used to mean fourteen
    // requests — and then a 429 storm.
    for (let index = 0; index < 14; index += 1) {
      mockedUseCockpitRun.mockReturnValue(baseCockpitRun({
        events: Array.from({ length: index + 1 }, (_, i) => ({
          type: 'node_completed' as const, run_id: 'run-123', node_id: `n${i}`, output_preview: '', ts: '2026-08-14T17:21:00Z',
        })),
      }));
      rerender(<BusinessView />);
    }
    await vi.advanceTimersByTimeAsync(1500);

    expect(vi.mocked(api.businessProjection).mock.calls.length).toBeLessThanOrEqual(2);
    vi.useRealTimers();
  });

  it('keeps the last known state and explains itself when rate limited', async () => {
    vi.mocked(api.businessProjection)
      .mockResolvedValueOnce(basfProjection())
      .mockRejectedValue(new Error('429 Rate limit exceeded'));
    const { rerender } = render(<BusinessView />);
    await screen.findByText('BASF SE — Quotation request');

    mockedUseCockpitRun.mockReturnValue(baseCockpitRun({
      events: [{ type: 'node_completed', run_id: 'run-123', node_id: 'n1', output_preview: '', ts: '2026-08-14T17:21:00Z' }],
    }));
    rerender(<BusinessView />);

    expect(await screen.findByText(/Showing the last known state/)).toBeInTheDocument();
    // The work item is still fully readable.
    expect(screen.getByText('Ready for Inside Sales')).toBeInTheDocument();
  });
});

describe('accessibility', () => {
  it('announces the status to assistive technology', async () => {
    await renderView();

    const live = screen.getByRole('status');
    expect(live).toHaveTextContent('Ready for Inside Sales');
    expect(live).toHaveTextContent('4 items need attention');
  });

  it('gives every activity toggle an expanded state', async () => {
    await renderView();

    const toggle = screen.getByRole('button', { name: /Handling checks completed/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });
});
