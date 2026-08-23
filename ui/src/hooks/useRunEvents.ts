import { useEffect, useState } from 'react';
import { streamRunEvents } from '../api/client';
import type { RunEvent } from '../api/types';

export function useRunEvents(runId: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    // This effect owns one authenticated SSE fetch/reconnect lifecycle.
     
    setEvents([]);
    setOpen(false);
    setError(null);

    const controller = new AbortController();
    let cancelled = false;
    let terminal = false;
    let lastEventId: number | undefined;
    let retryTimer: number | undefined;

    const run = async () => {
      let retryAttempt = 0;
      while (!cancelled && !terminal) {
        try {
          const result = await streamRunEvents(runId, {
            signal: controller.signal,
            lastEventId,
            onOpen: () => {
              if (!cancelled) {
                setOpen(true);
                setError(null);
              }
            },
            onEvent: (event) => {
              if (cancelled) return;
              if (event.event_id !== undefined) {
                lastEventId = event.event_id;
              }
              setEvents((previous) => {
                if (
                  event.event_id !== undefined
                  && previous.some(
                    item => item.event_id === event.event_id,
                  )
                ) {
                  return previous;
                }
                return [...previous, event];
              });
              if (
                event.type === 'run_completed'
                || event.type === 'run_rejected'
                || event.type === 'run_failed'
              ) {
                terminal = true;
              }
            },
          });
          lastEventId = result.lastEventId ?? lastEventId;
          terminal = terminal || result.terminal;
          if (!terminal && !cancelled) {
            throw new Error('SSE stream closed before a terminal event');
          }
        } catch (streamError) {
          if (cancelled || controller.signal.aborted) return;
          setOpen(false);
          setError(
            streamError instanceof Error
              ? streamError.message
              : 'SSE connection error',
          );
          retryAttempt += 1;
          const delay = Math.min(1000 * (2 ** (retryAttempt - 1)), 8000);
          await new Promise<void>((resolve) => {
            retryTimer = window.setTimeout(resolve, delay);
          });
        }
      }
      if (!cancelled) setOpen(false);
    };

    void run();
    return () => {
      cancelled = true;
      controller.abort();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [runId]);

  return { events, open, error };
}
