import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AskAiPanel } from './AskAiPanel';
import { api } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  api: {
    runChatHistory: vi.fn(),
    askAboutRun: vi.fn(),
  },
}));

// jsdom doesn't implement scrollIntoView; AskAiPanel calls it to keep the
// latest turn in view, which is irrelevant to what this test checks.
Element.prototype.scrollIntoView = vi.fn();

/**
 * Regression coverage for the "answers from the wrong run" bug: a run's
 * conversation must never survive into a request for a *different* run.
 *
 * Both call sites (BusinessView's chat modal, RunHistory's Ask AI tab) reuse
 * this component across a change of `runId` without necessarily unmounting
 * it on their own — RunHistory in particular keeps the "ask-ai" tab active
 * across a run selection change. The fix is a `key={runId}` at each call
 * site, which forces React to fully unmount and remount this component
 * instead of reusing the instance — clearing `turns` before any further
 * question can be asked. This test exercises that remount directly, the way
 * a `key` change actually behaves.
 */
describe('AskAiPanel keyed remount', () => {
  beforeEach(() => {
    vi.mocked(api.runChatHistory).mockReset();
    vi.mocked(api.askAboutRun).mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it('does not carry the previous run\'s conversation into a new run', async () => {
    vi.mocked(api.runChatHistory).mockResolvedValueOnce({
      turns: [],
      starter_questions: ['Summary of this task'],
    });
    vi.mocked(api.askAboutRun).mockResolvedValueOnce({
      turns: [
        { role: 'user', content: 'Who is the customer?', ts: 1 },
        { role: 'assistant', content: 'BASF SE.', ts: 2 },
      ],
      answer: 'BASF SE.',
    });
    const user = userEvent.setup();

    const { rerender } = render(<AskAiPanel key="run-A" runId="run-A" />);
    await screen.findByPlaceholderText('Ask anything about this run…');
    await user.type(screen.getByPlaceholderText('Ask anything about this run…'), 'Who is the customer?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('BASF SE.')).toBeInTheDocument();
    expect(api.runChatHistory).toHaveBeenCalledWith('run-A');

    // A different key, as a real caller supplies via `key={runId}`, forces a
    // full unmount/remount rather than reusing this instance with a new prop.
    vi.mocked(api.runChatHistory).mockResolvedValueOnce({
      turns: [],
      starter_questions: ['Summary of this task'],
    });
    rerender(<AskAiPanel key="run-B" runId="run-B" />);

    await waitFor(() => expect(api.runChatHistory).toHaveBeenCalledWith('run-B'));
    // The prior run's turn is gone — no leftover conversation to send as
    // history alongside the new run's own data.
    expect(screen.queryByText('BASF SE.')).not.toBeInTheDocument();
    expect(screen.queryByText('Who is the customer?')).not.toBeInTheDocument();

    vi.mocked(api.askAboutRun).mockResolvedValueOnce({
      turns: [
        { role: 'user', content: 'Who is the customer?', ts: 3 },
        { role: 'assistant', content: 'Siemens AG.', ts: 4 },
      ],
      answer: 'Siemens AG.',
    });
    await user.type(screen.getByPlaceholderText('Ask anything about this run…'), 'Who is the customer?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(api.askAboutRun).toHaveBeenCalledWith('run-B', 'Who is the customer?', []));
  });
});
