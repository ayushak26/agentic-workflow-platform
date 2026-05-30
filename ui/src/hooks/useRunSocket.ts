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

    let terminal = false;
    let cancelled = false;

    console.log('[WS] opening for', runId);
    const ws = new WebSocket(wsUrl(runId));

    ws.onopen = () => { if (!cancelled) { console.log('[WS] open'); setOpen(true); } };
    ws.onmessage = (m) => {
      if (cancelled) return;
      const evt = JSON.parse(m.data) as RunEvent;
      console.log('[WS] event', evt.type, (evt as any).node_id ?? '');
      setEvents((prev) => [...prev, evt]);
      if (evt.type === 'run_completed' || evt.type === 'run_failed') terminal = true;
    };
    ws.onerror = () => { if (!cancelled && !terminal) setError('WebSocket error'); };
    ws.onclose = (e) => {
      console.log('[WS] closed', { code: e.code, reason: e.reason, cancelled, terminal });
      if (!cancelled) setOpen(false);
    };

    return () => {
      console.log('[WS] cleanup → cancelled=true for', runId);
      cancelled = true;
      ws.close();
    };
  }, [runId]);

  return { events, open, error };
}