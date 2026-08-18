import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPreviewPanel } from './ChatPreviewPanel';
import { api } from '../../../api/client';
import type { YamlWorkflow } from '../yaml-bridge';

vi.mock('../../../api/client', () => ({
  api: {
    simulateWorkflow: vi.fn(),
  },
}));

function workflow(startConfig: Record<string, unknown> = {}): YamlWorkflow {
  return {
    name: 'test',
    nodes: [
      { id: 'begin', type: 'StartAgent', config: { mode: 'chatbot', ...startConfig } },
      { id: 'finish', type: 'EndAgent', config: { mode: 'chat_response' } },
    ],
    edges: [{ from: 'begin', to: 'finish' }],
  } as YamlWorkflow;
}

describe('ChatPreviewPanel', () => {
  beforeEach(() => {
    vi.mocked(api.simulateWorkflow).mockReset();
  });

  it('shows a friendly empty state when the Start node is not in chatbot mode', () => {
    render(<ChatPreviewPanel workflow={{ name: 'test', nodes: [], edges: [] } as unknown as YamlWorkflow} workflowYaml="" />);
    expect(screen.getByText(/Chatbot Interface mode/)).toBeInTheDocument();
  });

  it('shows the welcome message and suggested questions before any turn', () => {
    render(
      <ChatPreviewPanel
        workflow={workflow({ welcome_message: 'Hello! How can I help?', suggested_questions: ['Where is the manual?'] })}
        workflowYaml="yaml"
      />,
    );
    expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument();
    expect(screen.getByText('Where is the manual?')).toBeInTheDocument();
  });

  it('renders a plain reply with its sources', async () => {
    vi.mocked(api.simulateWorkflow).mockResolvedValue({
      simulation_id: 'sim-1',
      status: 'completed',
      duration_s: 0.2,
      steps: [],
      path: ['begin', 'finish'],
      output: {
        outcome: 'reply',
        message: 'The MX-400 shuts down when thermal protection triggers.',
        sources: [{ file_name: 'MX-400 Guide.pdf', locations: [{ page: 12, section: null }] }],
      },
    });

    const user = userEvent.setup();
    render(<ChatPreviewPanel workflow={workflow()} workflowYaml="yaml" />);
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'Why does it overheat?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText(/thermal protection triggers/)).toBeInTheDocument();
    });
    expect(screen.getByText('MX-400 Guide.pdf')).toBeInTheDocument();
    expect(screen.queryByText(/Routed to/)).not.toBeInTheDocument();
  });

  it('shows a routing badge with a humanized label when route_to_label is not configured', async () => {
    vi.mocked(api.simulateWorkflow).mockResolvedValue({
      simulation_id: 'sim-2',
      status: 'completed',
      duration_s: 0.2,
      steps: [],
      path: ['begin', 'finish'],
      output: {
        outcome: 'route',
        message: "I'll forward this to Customer Support.",
        route_to: 'customer_support',
      },
    });

    const user = userEvent.setup();
    render(<ChatPreviewPanel workflow={workflow()} workflowYaml="yaml" />);
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'Still overheating');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText('✓ Routed to Customer Support')).toBeInTheDocument();
    });
  });

  it('shows a friendly message, not a raw error, when the workflow fails', async () => {
    vi.mocked(api.simulateWorkflow).mockResolvedValue({
      simulation_id: 'sim-3',
      status: 'failed',
      duration_s: 0.1,
      steps: [],
      path: [],
      error: 'RuntimeError: node "begin" raised ValueError',
    });

    const user = userEvent.setup();
    render(<ChatPreviewPanel workflow={workflow()} workflowYaml="yaml" />);
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'hi');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText('The workflow could not complete this request.')).toBeInTheDocument();
    });
    // Technical detail sits behind a collapsed disclosure, not shown by default.
    const detail = screen.getByText('View error').closest('details');
    expect(detail?.hasAttribute('open')).toBe(false);
  });

  it('resets the conversation without changing the workflow', async () => {
    vi.mocked(api.simulateWorkflow).mockResolvedValue({
      simulation_id: 'sim-4',
      status: 'completed',
      duration_s: 0.1,
      steps: [],
      path: [],
      output: { outcome: 'reply', message: 'Answer one.' },
    });

    const user = userEvent.setup();
    render(<ChatPreviewPanel workflow={workflow()} workflowYaml="yaml" />);
    await user.type(screen.getByPlaceholderText('Ask a question...'), 'q1');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText('Answer one.')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'New Conversation' }));
    expect(screen.queryByText('Answer one.')).not.toBeInTheDocument();
    expect(screen.queryByText('q1')).not.toBeInTheDocument();
  });
});
