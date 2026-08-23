import { streamRunEvents } from '../../../api/client';
import type { RunEvent } from '../../../api/types';

export type ChatRunObserverOptions = {
  signal: AbortSignal;
  onOpen?: () => void;
  onEvent: (event: RunEvent) => void;
  onDisconnected?: (error: Error) => void;
};

/**
 * Observe one attempt until a terminal run event. A node_paused event is not
 * terminal: the same subscription remains alive and receives events emitted
 * after either a cooperative resume or a HITL decision.
 */
export async function observeChatRun(
  runId: string,
  options: ChatRunObserverOptions,
): Promise<void> {
  let lastEventId: number | undefined;
  let retryAttempt = 0;
  while (!options.signal.aborted) {
    try {
      const result = await streamRunEvents(runId, {
        signal: options.signal,
        lastEventId,
        onOpen: () => {
          retryAttempt = 0;
          options.onOpen?.();
        },
        onEvent: event => {
          if (event.event_id !== undefined) lastEventId = event.event_id;
          options.onEvent(event);
        },
      });
      lastEventId = result.lastEventId ?? lastEventId;
      if (result.terminal || options.signal.aborted) return;
      throw new Error('Run event stream closed before the attempt finished.');
    } catch (reason) {
      if (options.signal.aborted) return;
      const error = reason instanceof Error ? reason : new Error(String(reason));
      options.onDisconnected?.(error);
      retryAttempt += 1;
      const delay = Math.min(500 * (2 ** (retryAttempt - 1)), 4_000);
      await new Promise<void>(resolve => window.setTimeout(resolve, delay));
    }
  }
}