import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../../../api/client';
import type { BusinessNarration, BusinessProjection, RunEvent } from '../../../api/types';

// The previous version re-fetched the projection once per SSE event. A run
// with fourteen nodes emits twenty-eight of them in a few seconds, which
// exhausted the 60-requests-per-minute budget and then kept spending it on
// 429s — so a live work item ended up showing less than a finished one.
//
// Three rules fix it, and they matter in this order:
//   1. Coalesce. Events arrive in bursts; a burst is one refetch, taken after
//      it settles.
//   2. Never overlap. A refetch requested while one is in flight sets a flag
//      and runs once, afterwards — not a second request.
//   3. Back off, don't hammer. A 429 stops polling until the window the server
//      named has passed, and the last good projection stays on screen.
const BURST_WINDOW_MS = 700;
const MIN_INTERVAL_MS = 2_000;
const DEFAULT_BACKOFF_MS = 15_000;

/**
 * Events that can change what a business user sees.
 *
 * `node_started` is excluded on purpose: a node beginning changes no business
 * fact, no status and no attention item, and it is exactly half of the traffic
 * the old version generated.
 */
const SIGNIFICANT_EVENTS = new Set<RunEvent['type']>([
  'node_completed', 'node_reused', 'node_paused',
  'run_completed', 'run_failed', 'run_rejected',
]);

function significantEventCount(events: RunEvent[]): number {
  return events.filter(event => SIGNIFICANT_EVENTS.has(event.type)).length;
}

// The client surfaces a failed response as `"<status> <detail>"`, and the
// Retry-After header does not survive that, so the backoff is chosen here and
// doubled on each consecutive rejection up to the server's one-minute window.
function isRateLimited(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.startsWith('429') || message.includes('Rate limit exceeded');
}

export interface BusinessProjectionState {
  projection: BusinessProjection | null;
  error: string | null;
  /** True only for the first load; a refresh never blanks the screen. */
  loading: boolean;
  throttled: boolean;
  refetch: () => void;
}

export function useBusinessProjection(
  runId: string | undefined,
  events: RunEvent[],
  gateNodeId: string | undefined,
  finishedStatus: string | undefined,
): BusinessProjectionState {
  const [projection, setProjection] = useState<BusinessProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [throttled, setThrottled] = useState(false);
  const [manualCount, setManualCount] = useState(0);

  const inFlight = useRef(false);
  const queued = useRef(false);
  const lastFetchAt = useRef(0);
  const blockedUntil = useRef(0);
  const backoff = useRef(0);
  const alive = useRef(true);
  // Holds the current fetch so the queued-retry path can re-enter it without
  // the callback referencing itself — a self-reference defeats memoization.
  const again = useRef<() => void>(() => {});

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = useCallback(async () => {
    if (!runId) return;
    if (inFlight.current) { queued.current = true; return; }
    if (Date.now() < blockedUntil.current) { queued.current = true; return; }

    inFlight.current = true;
    lastFetchAt.current = Date.now();
    try {
      const next = await api.businessProjection(runId);
      if (!alive.current) return;
      setProjection(next);
      setError(null);
      setThrottled(false);
      backoff.current = 0;
    } catch (e) {
      if (!alive.current) return;
      if (isRateLimited(e)) {
        // Keep whatever is already on screen: a stale-by-seconds work item is
        // far more useful than an error page.
        backoff.current = Math.min(60_000, backoff.current * 2 || DEFAULT_BACKOFF_MS);
        blockedUntil.current = Date.now() + backoff.current;
        setThrottled(true);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      inFlight.current = false;
      if (queued.current && alive.current) {
        queued.current = false;
        const wait = Math.max(
          MIN_INTERVAL_MS - (Date.now() - lastFetchAt.current),
          blockedUntil.current - Date.now(),
          0,
        );
        setTimeout(() => { if (alive.current) again.current(); }, wait);
      }
    }
  }, [runId]);

  useEffect(() => { again.current = () => { void load(); }; }, [load]);

  const eventCount = significantEventCount(events);

  useEffect(() => {
    if (!runId) return;
    // First load is immediate; every later trigger waits out its burst.
    const delay = projection === null ? 0 : BURST_WINDOW_MS;
    const timer = setTimeout(() => { void load(); }, delay);
    return () => clearTimeout(timer);
    // `projection` is deliberately not a dependency — it is read to decide
    // "first load or refresh", and depending on it would re-trigger on every
    // successful fetch, which is the loop this hook exists to prevent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, eventCount, gateNodeId, finishedStatus, manualCount, load]);

  return {
    projection,
    error,
    loading: projection === null && error === null,
    throttled,
    refetch: useCallback(() => setManualCount(n => n + 1), []),
  };
}

/**
 * The status narration, fetched once per meaningful state change.
 *
 * Keyed on `state_version`, which the server computes from what a business
 * user would notice changing — so a re-render, a poll, or a run that merely
 * got slower never spends a model call. The projection already carries a
 * deterministic headline, so this only ever improves wording.
 */
export function useBusinessNarration(
  runId: string | undefined,
  stateVersion: string | undefined,
): BusinessNarration | null {
  const [narration, setNarration] = useState<BusinessNarration | null>(null);
  const requested = useRef<string | null>(null);

  useEffect(() => {
    if (!runId || !stateVersion || requested.current === stateVersion) return;
    requested.current = stateVersion;
    let cancelled = false;
    api.businessNarration(runId)
      .then(next => { if (!cancelled) setNarration(next); })
      // Silent: the deterministic headline is already on screen, and telling
      // someone their status line could have been phrased better helps nobody.
      .catch(() => {});
    return () => { cancelled = true; };
  }, [runId, stateVersion]);

  return narration && narration.state_version === stateVersion ? narration : null;
}
