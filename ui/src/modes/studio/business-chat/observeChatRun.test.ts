import { beforeEach, describe, expect, it, vi } from 'vitest';

import { streamRunEvents } from '../../../api/client';
import { observeChatRun } from './observeChatRun';

vi.mock('../../../api/client', () => ({ streamRunEvents: vi.fn() }));

const stream = vi.mocked(streamRunEvents);

describe('observeChatRun', () => {
  beforeEach(() => stream.mockReset());

  it('does not treat node_paused as terminal and reconnects from its event id', async () => {
    stream
      .mockImplementationOnce(async (_runId, options) => {
        options.onOpen();
        options.onEvent({
          type: 'node_paused', run_id: 'run-1', node_id: 'review', context: {},
          ts: 'now', event_id: 7,
        });
        return { lastEventId: 7, terminal: false };
      })
      .mockImplementationOnce(async (_runId, options) => {
        expect(options.lastEventId).toBe(7);
        options.onOpen();
        options.onEvent({ type: 'run_completed', run_id: 'run-1', ts: 'later', event_id: 8 });
        return { lastEventId: 8, terminal: true };
      });

    const events: string[] = [];
    await observeChatRun('run-1', {
      signal: new AbortController().signal,
      onEvent: event => events.push(event.type),
    });

    expect(events).toEqual(['node_paused', 'run_completed']);
    expect(stream).toHaveBeenCalledTimes(2);
  });

  it('returns without reconnecting after a terminal event', async () => {
    stream.mockImplementationOnce(async (_runId, options) => {
      options.onEvent({ type: 'run_failed', run_id: 'run-1', error: 'boom', ts: 'now' });
      return { terminal: true };
    });
    await observeChatRun('run-1', {
      signal: new AbortController().signal,
      onEvent: vi.fn(),
    });
    expect(stream).toHaveBeenCalledOnce();
  });
});