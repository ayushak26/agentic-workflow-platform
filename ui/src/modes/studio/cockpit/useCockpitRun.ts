/* Runtime node payloads are intentionally plugin-defined and heterogeneous. */
/* eslint-disable @typescript-eslint/no-explicit-any */
// Extracted, behavior-preserving, from the original Cockpit.tsx: owns the
// run/resume trigger, SSE subscription, run-detail polling, HITL gate
// state, attach-mode reconstruction, and pipeline-stage bookkeeping. None
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
import type { HITLReviewContent, PipelineRunDetail, RunDetail } from '../../../api/types';
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

// Present when this Cockpit is running one stage of a pipeline rather than a
// standalone workflow. 'start' triggers POST /pipelines/run (stage 0 of a
// fresh pipeline run); 'advance' triggers POST /pipelines/{id}/advance (any
// later stage, reached via the "Continue to next stage" action).
export type PipelineNavState = {
  mode: 'start' | 'advance';
  pipelineYaml?: string;
  pipelineRunId: string;
  pipelineName?: string;
  stageId: string;
  stageIndex: number;
  totalStages: number;
};

export type CockpitNavState = {
  workflowYaml?: string;
  inputs?: Record<string, unknown>;
  workflowName?: string;
  retrySourceRunId?: string;
  pipeline?: PipelineNavState;
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
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [liveRun, setLiveRun] = useState<RunDetail | null>(null);

  // HITL gates and final result are driven off the run/resume HTTP responses,
  // NOT the SSE feed — so approvals work even if the stream drops mid-run.
  const [gate, setGate] = useState<Gate | null>(null);
  const [finished, setFinished] = useState<Finished | null>(null);
  const setRunCost = useSetRunCost();

  // The review panel closes the instant a decision is submitted (resume is
  // synchronous and can block for the rest of the run) or the user
  // dismisses it manually, revealing the full graph either way. It reopens
  // by itself the moment a genuinely different node's gate appears — a
  // fresh pause always deserves the human's attention by default.
  const [gateHidden, setGateHidden] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
        .then(c => setRunCost(c.total_usd))
        .catch(e => console.error('cost fetch failed', e));
    }
  }, [finished, runId, setRunCost]);

  // Subscribe first, then trigger exactly once so no early SSE event is lost.
  useEffect(() => {
    if (navState.attach || !streamOpen || runTriggered || !navState.workflowYaml || !runId) return;
    // This state guards the one external run request owned by this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRunTriggered(true);
    const pipeline = navState.pipeline;
    const request = pipeline
      ? pipeline.mode === 'advance'
        ? api.advancePipeline(pipeline.pipelineRunId, undefined, runId)
        : api.runPipeline(
            pipeline.pipelineYaml!,
            navState.inputs ?? {},
            undefined,
            pipeline.pipelineRunId,
            runId,
          )
      : navState.retrySourceRunId
      ? api.retryFailedRun(navState.retrySourceRunId, runId)
      // Do not send a made-up "default" session. The API derives the durable
      // history/retrieval scope from the authenticated user.
      : api.runWorkflow(
          navState.workflowYaml,
          navState.inputs ?? {},
          undefined,
          runId,
        );
    request
      // A pipeline call's result is nested under stage_result — the same
      // {status, run_id, state, ...} shape run_workflow returns directly.
      .then((res) => applyResumeResult(pipeline ? (res as any).stage_result : res))
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
    navState.pipeline,
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

  // Attach mode never gets a run/resume HTTP response of its own to read a
  // terminal status off (see applyResumeResult) — it has to notice the run
  // finished via the same runDetail poll that drives the Variables panel.
  useEffect(() => {
    if (!navState.attach || finished) return;
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFinished(result);
  }, [navState.attach, liveRun, finished]);

  // Attach mode's HITL gate isn't known from any live HTTP response either —
  // reconstruct it from the durable checkpoint the same way a fresh page
  // load must (see GET .../pending-gate). A "user_requested" pause has no
  // gate to review — Run History's own pause/resume buttons already cover
  // that case — so this only ever populates `gate` for a real HITL node.
  const [gateFetchError, setGateFetchError] = useState<string | null>(null);
  const [gateRetryToken, setGateRetryToken] = useState(0);
  useEffect(() => {
    if (!navState.attach || !runId || finished || gate) return;
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
  }, [navState.attach, runId, finished, gate, liveRun?.status, liveRun?.pause_kind, gateRetryToken]);

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

  // Pipeline mode: keep the pipeline's own gate/advance state alongside this
  // stage's run so the banner can offer "Continue to next stage" as soon as
  // this stage finishes. Re-fetched (not read off the trigger response) so it
  // stays correct even when this stage finished via a mid-run HITL resume,
  // which doesn't return the pipeline doc itself.
  const [pipelineDoc, setPipelineDoc] = useState<PipelineRunDetail | null>(null);
  const [continuingStage, setContinuingStage] = useState(false);
  const [continueError, setContinueError] = useState<string | null>(null);
  const pipelineRunId = navState.pipeline?.pipelineRunId;
  useEffect(() => {
    if (!pipelineRunId) return;
    let cancelled = false;
    api.pipelineRunDetail(pipelineRunId)
      .then((doc) => { if (!cancelled) setPipelineDoc(doc); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [pipelineRunId, finished]);

  const continueToNextStage = useCallback(async () => {
    if (!pipelineDoc) return;
    const nextIndex = pipelineDoc.current_stage_index + 1;
    const nextStage = pipelineDoc.stages[nextIndex];
    if (!nextStage) return;
    setContinuingStage(true);
    setContinueError(null);
    try {
      const { yaml: stageYaml } = await api.getWorkflow(nextStage.workflow);
      const stageRunId = crypto.randomUUID();
      navigate(`/cockpit/${stageRunId}`, {
        state: {
          workflowYaml: stageYaml,
          workflowName: nextStage.id,
          pipeline: {
            mode: 'advance',
            pipelineRunId: pipelineDoc.pipeline_run_id,
            pipelineName: pipelineDoc.pipeline_name,
            stageId: nextStage.id,
            stageIndex: nextIndex,
            totalStages: pipelineDoc.stages.length,
          },
        },
      });
    } catch (e: unknown) {
      setContinueError(e instanceof Error ? e.message : String(e));
      setContinuingStage(false);
    }
  }, [pipelineDoc, navigate]);

  const retryGateFetch = useCallback(() => {
    setGateFetchError(null);
    setGateRetryToken((value) => value + 1);
  }, []);

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
    pipelineDoc,
    continueToNextStage,
    continuingStage,
    continueError,
    liveRunNodeStatus: (nodeId: string) => liveRunNodeStatus(nodeId, liveRun),
  };
}
