import { useEffect, useState } from 'react';
import { api, wsUrl } from '../api/client';
import type { RunEvent } from '../api/types';

export function useRunSocket(runId: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    // Reset state because this effect owns a new external WebSocket lifecycle.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEvents([]);
    setOpen(false);
    setError(null);

    let terminal = false;
    let cancelled = false;

    let ws: WebSocket | null = null;
    void api.websocketTicket(runId)
      .then(({ ticket }) => {
        if (cancelled) return;
        ws = new WebSocket(wsUrl(runId, ticket));
        ws.onopen = () => { if (!cancelled) setOpen(true); };
        ws.onmessage = (message) => {
          if (cancelled) return;
          const evt = JSON.parse(message.data) as RunEvent;
          setEvents((prev) => [...prev, evt]);
          if (evt.type === 'run_completed' || evt.type === 'run_failed') {
            terminal = true;
          }
        };
        ws.onerror = () => {
          if (!cancelled && !terminal) setError('WebSocket error');
        };
        ws.onclose = () => {
          if (!cancelled) setOpen(false);
        };
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : 'Could not authorize live events',
          );
        }
      });

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [runId]);

  return { events, open, error };
}
