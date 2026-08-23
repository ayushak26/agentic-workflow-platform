/* Runtime node payloads are intentionally plugin-defined and heterogeneous. */
/* eslint-disable @typescript-eslint/no-explicit-any */
// Extracted, behavior-preserving, from the original Cockpit.tsx: owns the
// run/resume trigger, SSE subscription, run-detail polling, HITL gate
// state, and attach-mode reconstruction. None
// of this changed during the Cockpit redesign — only the presentation
// (graph/panels) around it did — so this hook is a straight lift, not a
// rewrite, to keep the existing execution behavior intact.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../../api/client';
import { useRunEvents } from '../../../hooks/useRunEvents';
import { useSetRunCost } from '../../../RunCostContext';
import { deriveCockpitState } from '../cockpit-state';
import { parseYaml, type YamlWorkflow } from '../yaml-bridge';
import { NODE_RUN_STATUS_MAP } from './node-render';
import type { HITLReviewContent, RunCostSummary, RunDetail } from '../../../api/types';
import type { NodeStatus } from '../cockpit-state';

export type Gate = {
  nodeId: string;
  context: unknown;
  question: string;
  allowedActions: string[];
  content: HITLReviewContent | null;
  allowDocumentOverride: boolean;
  maxEditChars: number;
};

export type Finished = {
  status: 'completed' | 'failed' | 'rejected';
  state?: any;
  output?: Record<string, unknown>;
  error?: string;
  node?: string;
  reason?: string;
};

export type CockpitNavState = {
  workflowYaml?: string;
  inputs?: Record<string, unknown>;
  workflowName?: string;
  retrySourceRunId?: string;
  // Reopening an existing run (Run History's "Open in Cockpit") rather
  // than launching a new one. Skips triggering run/resume/pipeline — this
  // run already exists — and instead reconstructs graph + HITL gate state
  // from durable data (SSE replay + polled run detail + the pending-gate
  // endpoint) so it works even from a fresh page load.
  attach?: boolean;
  // The node selected in Run History when the user clicked "Open in
  // Cockpit" — preselected and focused once the graph lays out, so
  // switching between the two screens doesn't lose the node you were
  // looking at.
  selectedNodeId?: string;
  // Present only when this run was launched from the Workflow Builder
  // ("Run in Cockpit" / a node or branch test). Lets Cockpit show a "Back
  // to Builder" action that restores the same workflow, selection, and
  // viewport instead of just navigating to the Library.
  builderReturnPath?: string;
  viewport?: { x: number; y: number; zoom: number };
  // e.g. "Node test: reviewer" / "Branch test: approve" — shown instead of
  // the plain workflow name so a test run is never mistaken for a full run.
  testLabel?: string;
  // A fresh full-workflow Cockpit can wait for an explicit Test or Run choice.
  // Attach/retry/pipeline and synthetic node-test flows remain automatic.
  awaitLaunch?: boolean;
};

// Fallback node coloring for attach mode, when SSE replay has nothing (the
// event bus is a bounded in-memory buffer and may have evicted this run's
// history, or the backend process restarted since it paused).
function liveRunNodeStatus(nodeId: string, liveRun: RunDetail | null): NodeStatus | null {
  if (!liveRun) return null;
  const nodeRunStatus = liveRun.node_runs?.[nodeId]?.status;
  if (nodeRunStatus) return NODE_RUN_STATUS_MAP[nodeRunStatus];
  if (liveRun.active_nodes?.includes(nodeId)) return 'active';
  if (nodeId in (liveRun.outputs ?? {})) return 'done';
  return null;
}

export function useCockpitRun() {
  const { runId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  // Snapshot navigation state ONCE so the component binds to a stable run for its
  // whole life — re-deriving it each render could remount the SSE stream.
  const [navState] = useState(() => (location.state ?? {}) as CockpitNavState);

  const [parsedWf] = useState<YamlWorkflow | null>(() => (
    navState.workflowYaml ? parseYaml(navState.workflowYaml) : null
  ));
  const [runTriggered, setRunTriggered] = useState(false);
  const [launchRequested, setLaunchRequested] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [liveRun, setLiveRun] = useState<RunDetail | null>(null);

  // HITL gates and final result are driven off the run/resume HTTP responses,
  // NOT the SSE feed — so approvals work even if the stream drops mid-run.
  const [gate, setGate] = useState<Gate | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const setRunCost = useSetRunCost();
  const [costSummary, setCostSummary] = useState<RunCostSummary | null>(null);

  // The review panel closes the instant a decision is submitted (resume is
  // synchronous and can block for the rest of the run) or the user
  // dismisses it manually, revealing the full graph either way. It reopens
  // by itself the moment a genuinely different node's gate appears — a
  // fresh pause always deserves the human's attention by default.
  const [gateHidden, setGateHidden] = useState(false);
  useEffect(() => {

    setGateHidden(false);
  }, [gate?.nodeId]);

  const {
    events,
    open: streamOpen,
    error: streamError,
  } = useRunEvents(runId ?? null);

  // Apply a run/resume response: advance to the next gate, or finish.
  function applyResumeResult(res: any) {
    if (!res) return;
    if (res.status === 'paused') {
      const interrupt = (
        res.state?.__interrupt__?.[0]
        ?? res.interrupt?.[0]
        ?? null
      );
      const v = interrupt?.value ?? interrupt;
      if (v) {
        setGate({
          nodeId: v.node_id,
          context: v.context,
          question: v.question ?? '',
          allowedActions: v.allowed_actions ?? ['approve', 'reject'],
          content: v.content ?? null,
          allowDocumentOverride: v.allow_document_override ?? true,
          maxEditChars: v.max_edit_chars ?? 1_000_000,
        });
      }
    } else if (res.status === 'completed') {
      setGate(null);
      setFinished({
        status: 'completed',
        state: res.state,
        output: res.output,
      });
    } else if (res.status === 'failed') {
      setGate(null);
      setFinished({ status: 'failed', error: res.error });
    } else if (res.status === 'rejected') {
      setGate(null);
      setFinished({ status: 'rejected', node: res.node_id, reason: res.reason });
    }
  }

  // Fetch run cost whenever the run reaches a completed state, regardless of
  // which path (HTTP resume vs SSE) marked it finished.
  useEffect(() => {
    if (finished?.status === 'completed' && runId) {
      api.costForRun(runId)
        .then(c => {
          setRunCost(c);
          setCostSummary(c);
        })
        .catch(e => console.error('cost fetch failed', e));
    }
  }, [finished, runId, setRunCost]);

  // Subscribe first, then trigger exactly once so no early SSE event is lost.
  useEffect(() => {
    if (
      navState.attach
      || !streamOpen
      || runTriggered
      || !navState.workflowYaml
      || !runId
      || (navState.awaitLaunch && !launchRequested)
    ) return;
    // This state guards the one external run request owned by this effect.

    setRunTriggered(true);
    const request = navState.retrySourceRunId
      ? api.retryFailedRun(navState.retrySourceRunId, runId)
      // Do not send a made-up "default" session. The API derives the durable
      // history/retrieval scope from the authenticated user.
      : api.runWorkflow(
          navState.workflowYaml,
          navState.inputs ?? {},
          {
            run_id: runId,
            skip_preflight: navState.awaitLaunch === true,
            origin: navState.builderReturnPath ? 'builder' : 'direct',
          },
        );
    request
      .then(applyResumeResult)
      .catch((e) => {
        const message = String(e.message ?? e);
        setTriggerError(message);
        setFinished({ status: 'failed', error: message });
      });
  }, [
    navState.attach,
    streamOpen,
    runTriggered,
    navState.workflowYaml,
    navState.inputs,
    navState.retrySourceRunId,
    navState.awaitLaunch,
    navState.builderReturnPath,
    launchRequested,
    runId,
  ]);

  // Exact inputs and completed outputs are persisted incrementally in run
  // history. Polling that record powers the Variables panel while the graph is
  // still executing; SSE previews remain intentionally small.
  useEffect(() => {
    if (!runId || finished) return;
    if (!runTriggered && !navState.attach) return;
    let cancelled = false;
    const load = () => {
      api.runDetail(runId)
        .then((result) => {
          if (!cancelled) setLiveRun(result.run);
        })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [finished, runId, runTriggered, navState.attach]);

  // A fresh POST /workflows/run now returns {status: 'running'} almost
  // immediately — execution happens in a detached background task so a
  // dropped/slow HTTP connection can't cancel a multi-minute run mid-call
  // (see launch_background_run on the backend). So neither a fresh launch
  // nor attach mode (reopening an existing run) gets a terminal status off
  // the trigger response anymore (see applyResumeResult) — both notice the
  // run finished via the same runDetail poll that drives the Variables
  // panel. Resume/pipeline endpoints still resolve synchronously with a
  // terminal result via applyResumeResult, which sets `finished` first and
  // short-circuits this effect.
  useEffect(() => {
    if ((!runTriggered && !navState.attach) || finished) return;
    const result: Finished | null = (
      liveRun?.status === 'completed'
        ? {
          status: 'completed',
          state: {
            node_outputs: liveRun.outputs,
            inputs: liveRun.inputs,
            variables: liveRun.variables,
          },
        }
        : liveRun?.status === 'failed'
        ? { status: 'failed', error: liveRun.error ?? undefined }
        : liveRun?.status === 'rejected'
        ? {
          status: 'rejected',
          node: liveRun.failed_node ?? undefined,
          reason: liveRun.error ?? undefined,
        }
        : null
    );
    if (!result) return;
    // Synchronizing local state from a polled external record (runDetail),
    // not from a prop/state change this render already reflects.

    setFinished(result);
  }, [runTriggered, navState.attach, liveRun, finished]);

  // Neither a fresh launch nor attach mode gets a HITL gate off a live HTTP
  // response anymore (see the effect above) — reconstruct it from the
  // durable checkpoint the same way a fresh page load must (see GET
  // .../pending-gate). A "user_requested" pause has no gate to review — Run
  // History's own pause/resume buttons already cover that case — so this
  // only ever populates `gate` for a real HITL node.
  const [gateFetchError, setGateFetchError] = useState<string | null>(null);
  const [gateRetryToken, setGateRetryToken] = useState(0);
  useEffect(() => {
    if ((!runTriggered && !navState.attach) || !runId || finished || gate) return;
    if (liveRun?.status !== 'paused' || liveRun.pause_kind === 'user_requested') return;
    let cancelled = false;
    api.pendingGate(runId)
      .then((res) => {
        if (cancelled) return;
        if (!res.paused || res.pause_kind !== 'hitl_gate') return;
        setGateFetchError(null);
        setGate({
          nodeId: res.node_id,
          context: res.context,
          question: res.question,
          allowedActions: res.allowed_actions,
          content: res.content,
          allowDocumentOverride: res.allow_document_override,
          maxEditChars: res.max_edit_chars,
        });
      })
      .catch((e) => {
        if (!cancelled) setGateFetchError(String(e.message ?? e));
      });
    return () => { cancelled = true; };
  }, [runTriggered, navState.attach, runId, finished, gate, liveRun?.status, liveRun?.pause_kind, gateRetryToken]);

  // Derive node colors from SSE events (best-effort animation).
  const cockpit = useMemo(() => {
    const nodeIds = parsedWf?.nodes.map((n) => n.id) ?? [];
    return deriveCockpitState(nodeIds, events, streamOpen);
  }, [parsedWf, events, streamOpen]);

  const activeNodeId = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (
        event.type === 'node_started'
        && cockpit.nodeStates[event.node_id] === 'active'
      ) {
        return event.node_id;
      }
    }
    return liveRun?.active_nodes?.[0] ?? null;
  }, [cockpit.nodeStates, events, liveRun]);
  const reusedNodeCount = useMemo(
    () => events.filter((event) => event.type === 'node_reused').length,
    [events],
  );

  const retryGateFetch = useCallback(() => {
    setGateFetchError(null);
    setGateRetryToken((value) => value + 1);
  }, []);

  // Memoized so it's a stable reference across renders whenever `liveRun`
  // itself hasn't changed — Cockpit.tsx lists this in a useEffect's
  // dependency array, and a fresh closure every render would re-run that
  // effect every render, which calls setNodes with a new array reference
  // each time and triggers another render: an infinite render loop.
  const getLiveRunNodeStatus = useCallback(
    (nodeId: string) => liveRunNodeStatus(nodeId, liveRun),
    [liveRun],
  );

  return {
    runId,
    navState,
    parsedWf,
    navigate,
    triggerError,
    liveRun,
    gate,
    gateHidden,
    setGateHidden,
    gateFetchError,
    retryGateFetch,
    finished,
    events,
    streamOpen,
    streamError,
    cockpit,
    activeNodeId,
    reusedNodeCount,
    applyResumeResult,
    setTriggerError,
    liveRunNodeStatus: getLiveRunNodeStatus,
    costSummary,
    runTriggered,
    requestRun: () => setLaunchRequested(true),
  };
}
