import { useEffect, useRef, useState } from 'react';
import { api, wsUrl } from '../api/client';
import type { RunEvent, RunSnapshot } from '../api/types';

// Single source of truth: snapshot from REST + delta from WebSocket.
// On reconnect: refetch snapshot, then reattach socket. Never replay events.
export function useRunSocket(runId: string | null) {
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    (async () => {
      const snap = await api.runSnapshot(runId);
      if (cancelled) return;
      setSnapshot(snap);

      const ws = new WebSocket(wsUrl(runId));
      wsRef.current = ws;
      ws.onmessage = (m) => {
        const evt = JSON.parse(m.data) as RunEvent;
        setEvents((prev) => [...prev, evt]);
        setSnapshot((prev) => prev ? applyEvent(prev, evt) : prev);
      };
      ws.onerror = (e) => console.warn('ws error', e);
    })();

    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [runId]);

  return { snapshot, events };
}

function applyEvent(snap: RunSnapshot, e: RunEvent): RunSnapshot {
  switch (e.type) {
    case 'node_started':   return mut(snap, e.node_id, 'active');
    case 'node_completed': return mut(snap, e.node_id, 'done');
    case 'node_paused':    return mut({ ...snap, status: 'paused' }, e.node_id, 'paused');
    case 'run_completed':  return { ...snap, status: 'completed' };
    case 'run_failed':     return { ...snap, status: 'failed' };
  }
}
function mut(s: RunSnapshot, id: string, st: RunSnapshot['node_states'][string]): RunSnapshot {
  return { ...s, node_states: { ...s.node_states, [id]: st } };
}