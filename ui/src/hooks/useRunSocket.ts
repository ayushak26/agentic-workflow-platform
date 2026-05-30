import { useEffect, useState } from 'react';
import { wsUrl } from '../api/client';
import type { RunEvent } from '../api/types';

export function useRunSocket(runId: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    setOpen(false);
    setError(null);

    let terminal = false;    // saw run_completed / run_failed
    let cancelled = false;   // this socket was cleaned up (StrictMode or unmount)

    const ws = new WebSocket(wsUrl(runId));
    ws.onopen = () => { if (!cancelled) setOpen(true); };
    ws.onmessage = (m) => {
      if (cancelled) return;
      const evt = JSON.parse(m.data) as RunEvent;
      setEvents((prev) => [...prev, evt]);
      if (evt.type === 'run_completed' || evt.type === 'run_failed') terminal = true;
    };
    ws.onerror = () => {
      // Ignore errors from a cancelled socket or after the run already finished.
      if (!cancelled && !terminal) setError('WebSocket error');
    };
    ws.onclose = () => { if (!cancelled) setOpen(false); };

    return () => {
      cancelled = true;
      ws.close();
    };
  }, [runId]);

  return { events, open, error };
}